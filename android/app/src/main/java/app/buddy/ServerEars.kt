package app.buddy

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.util.Base64
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Ears that use Whisper on your own server instead of Google's recognizer.
 *
 * Records the microphone (16 kHz mono), finds each utterance with a simple
 * loudness detector that adapts to the room, and sends it to the server, which
 * sends back the text and the language it heard. The text then goes exactly
 * where the phone recognizer's would (talk, Listen-in, "Hey Buddy").
 *
 * Live hearing (Talk mode, when the server offers it): the utterance isn't kept
 * until you stop. It goes out a tenth of a second at a time while you speak, the
 * pause after it too, and the server says when the turn is over ([turnOver]):
 * it judges that from your words, not from a silence timer. Only if it stays
 * silent past [liveEndMs] does the phone end the turn itself, and the server
 * then hears it the old way from what it was sent.
 */
class ServerEars(
    private val earbudMic: () -> Boolean,  // earbud mic chosen: record the voice-call input
    private val send: (JSONObject) -> Boolean,
    private val onHeard: (text: String, final: Boolean) -> Unit,
    private val onActive: (Boolean) -> Unit,
    private val onProblem: (String) -> Unit,
    private val paused: () -> Boolean,   // e.g. while Buddy talks on the loudspeaker
    private val endSilenceMs: () -> Long = { 800L },  // "Pauses when I talk" in Settings
    private val noiseSuppress: () -> Boolean = { false },  // Settings: the phone's own noise suppressor
    private val vadFactory: () -> SileroVad? = { null },  // speech detection on the phone
    private val onSent: () -> Unit = {},  // a clip just left for the server: "heard you" feedback
    private val live: () -> Boolean = { false },  // the server hears Talk turns as they're spoken
    private val liveEndMs: () -> Long = { 3200L },  // silence after which the phone ends a live turn itself
) : Hearing {
    @Volatile private var cleaning: android.media.audiofx.NoiseSuppressor? = null
    @Volatile private var boost: android.media.audiofx.AutomaticGainControl? = null
    private val main = Handler(Looper.getMainLooper())
    private var thread: Thread? = null
    private var language = AUTO_LANGUAGE
    private var detected: String? = null

    @Volatile
    override var active = false
        private set

    override val currentLanguage: String
        get() = detected ?: language

    // Each start gets a new number; a recording thread stops as soon as its number isn't current.
    // (A quick stop-then-start used to leave the old thread running beside the new one, so every
    // sound was sent two or three times.)
    @Volatile private var generation = 0

    override fun start(lang: String) {
        if (active) return
        language = lang
        detected = null
        active = true
        onActive(true)
        val mine = ++generation
        val old = thread
        thread = Thread({ old?.join(1_500); if (mine == generation) record(mine) }, "buddy-server-ears").also { it.start() }
    }

    override fun stop() {
        if (!active) return
        active = false
        generation++
        onActive(false)
    }

    override fun destroy() = stop()

    @Volatile private var liveOver = ""

    /** The server called live turn [id] finished (or dropped it): stop sending it. */
    fun turnOver(id: String) {
        if (id.isNotEmpty()) liveOver = id
    }

    /** The server's answer for one utterance (main thread). */
    fun heard(text: String, lang: String) {
        if (!active || text.isBlank()) return
        if (lang.isNotEmpty() && language == AUTO_LANGUAGE) detected = lang
        onHeard(text, true)
    }

    @SuppressLint("MissingPermission") // the service only starts after the microphone is allowed
    private fun newRecorder(minBuffer: Int) = AudioRecord(
        if (earbudMic()) MediaRecorder.AudioSource.VOICE_COMMUNICATION else MediaRecorder.AudioSource.VOICE_RECOGNITION,
        RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minBuffer, RATE),
    ).also { recorder ->
        // The phone's own noise suppressor (fans, traffic, hum), if asked for and the phone has one.
        runCatching { cleaning?.release() }
        cleaning = if (noiseSuppress() && android.media.audiofx.NoiseSuppressor.isAvailable())
            runCatching { android.media.audiofx.NoiseSuppressor.create(recorder.audioSessionId)?.apply { enabled = true } }
                .getOrNull() else null
        // Listen-in on the phone's mic: raise quiet, distant voices before they're sent.
        runCatching { boost?.release() }
        boost = if (BuddyState.ui.value.mode == Mode.LISTEN_IN && !earbudMic() &&
            android.media.audiofx.AutomaticGainControl.isAvailable())
            runCatching { android.media.audiofx.AutomaticGainControl.create(recorder.audioSessionId)?.apply { enabled = true } }
                .getOrNull() else null
    }

    @SuppressLint("MissingPermission") // the service only starts after the microphone is allowed
    private fun record(mine: Int) {
        val minBuffer = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        var recorder: AudioRecord? = runCatching { newRecorder(minBuffer) }.getOrNull()
        if (recorder == null || recorder.state != AudioRecord.STATE_INITIALIZED) {
            Diag.event("mic", "recorder_failed", "mic" to if (earbudMic()) "headset" else "phone")
            main.post { onProblem("Couldn't open the microphone."); stop() }
            return
        }
        recorder.startRecording()
        var micErrors = 0
        // Speech detection on the phone; if the model can't load, the loudness check below stands in.
        val vad = runCatching { vadFactory() }.onFailure { android.util.Log.w("ServerEars", "no speech model", it) }
            .getOrNull()
        val frame = ShortArray(FRAME)
        val preRoll = ArrayDeque<ByteArray>()
        val utterance = ByteArrayOutputStream()
        var noise = 300.0
        var speaking = false
        var silentMs = 0
        var speechMs = 0
        var speechSq = 0.0  // diagnostics: this clip's loudness and peak
        var speechFrames = 0
        var peak = 0
        var noiseAtStart = 300.0
        var streaming = false  // this turn is going out as it's spoken
        var turn = ""          // and this is its id
        val chunk = ByteArrayOutputStream()
        var chunkFrames = 0
        var chunkSpeech = false
        try {
            while (active && mine == generation) {
                val n = recorder?.read(frame, 0, frame.size) ?: AudioRecord.ERROR_DEAD_OBJECT
                if (n < 0) {
                    // Another app or a call has the microphone (Android gives it to one app at a time):
                    // don't spin; wait, say so once, and reopen it when it's lost for good.
                    micErrors++
                    if (micErrors == 15) Diag.event("mic", "busy", "error" to n)
                    if (micErrors == 15) main.post { onProblem("The microphone is busy (a call or another app " +
                        "is using it). I'll keep trying.") }
                    Thread.sleep(200)
                    if (n == AudioRecord.ERROR_DEAD_OBJECT && micErrors % 10 == 0) {
                        runCatching { recorder?.release() }
                        recorder = runCatching { newRecorder(minBuffer) }.getOrNull()
                            ?.takeIf { it.state == AudioRecord.STATE_INITIALIZED }?.also { it.startRecording() }
                        if (recorder == null) {  // not free yet: keep the loop alive and try again later
                            Thread.sleep(1_000)
                            recorder = runCatching { newRecorder(minBuffer).also { it.startRecording() } }.getOrNull()
                            if (recorder == null) continue
                        }
                    }
                    continue
                }
                if (micErrors >= 15) main.post { onProblem("The microphone is back.") }
                micErrors = 0
                if (n == 0) continue
                if (paused()) {  // drop what we hear while Buddy itself is talking
                    if (speaking && streaming) sendLive("cancel", turn)
                    speaking = false; streaming = false; utterance.reset(); preRoll.clear()
                    chunk.reset(); chunkFrames = 0
                    continue
                }
                var sum = 0.0
                for (i in 0 until n) sum += frame[i].toDouble() * frame[i]
                val rms = sqrt(sum / n)
                // Listen-in: people across the table are quieter than you are into the mic, so a lower bar.
                val listen = BuddyState.ui.value.mode == Mode.LISTEN_IN
                val p = if (vad != null && n == FRAME) runCatching { vad.probability(frame) }.getOrNull() else null
                val loud = if (p != null) {
                    // Speech, not loudness: start at 0.5, keep going while above 0.35 (pauses between words).
                    // In Talk, a faint far-off voice isn't you: it must also be audible.
                    (if (speaking) p > 0.35f else p > 0.5f) && (listen || rms > 150.0)
                } else if (listen) rms > max(noise * 2.2, 250.0) else rms > max(noise * 3, 500.0)
                val bytes = toBytes(frame, n)
                if (!speaking) {
                    if (loud) {
                        speaking = true
                        silentMs = 0
                        speechMs = 0
                        speechSq = 0.0; speechFrames = 0; peak = 0; noiseAtStart = noise
                        preRoll.forEach { utterance.write(it) }
                        preRoll.clear()
                        utterance.write(bytes)
                        chunk.reset(); chunkFrames = 0; chunkSpeech = false
                        streaming = !listen && live()
                        if (streaming) {  // live: what's been heard so far leaves now, the rest as it comes
                            turn = java.lang.Long.toString(System.nanoTime() and 0xFFFFFFFFFFL, 36)
                            streaming = sendLive("start", turn, utterance.toByteArray())
                            if (streaming) utterance.reset()
                        }
                    } else {
                        noise = noise * 0.95 + rms * 0.05  // follow the room's background level
                        preRoll.addLast(bytes)
                        if (preRoll.size > PRE_ROLL_FRAMES) preRoll.removeFirst()
                    }
                    continue
                }
                if (streaming) {
                    chunk.write(bytes); chunkFrames++
                    if (loud) chunkSpeech = true
                    if (chunkFrames >= LIVE_CHUNK_FRAMES) {
                        sendLive("audio", turn, chunk.toByteArray(), chunkSpeech)
                        chunk.reset(); chunkFrames = 0; chunkSpeech = false
                    }
                } else utterance.write(bytes)
                if (loud) { speechSq += rms * rms; speechFrames++ }
                for (i in 0 until n) { val a = kotlin.math.abs(frame[i].toInt()); if (a > peak) peak = a }
                speechMs += FRAME_MS
                silentMs = if (loud) 0 else silentMs + FRAME_MS
                if (streaming) {
                    val over = liveOver == turn  // the server heard the end of this turn
                    // Live hearing went away mid-turn: the usual pause ends it, and the server hears it as a clip.
                    val limit = if (live()) max(liveEndMs(), endSilenceMs()) else endSilenceMs()
                    if (over || silentMs >= limit || speechMs >= LIVE_MAX_MS) {
                        val mic = if (earbudMic()) "headset" else "phone"
                        if (chunkFrames > 0 && !over) sendLive("audio", turn, chunk.toByteArray(), chunkSpeech)
                        if (over || speechMs - silentMs >= MIN_SPEECH_MS) {
                            val voiceDb = Diag.db(if (speechFrames > 0) sqrt(speechSq / speechFrames) else rms)
                            val noiseDb = Diag.db(noiseAtStart)
                            Diag.event("clip", "sent", "mic" to mic, "noise_db" to noiseDb, "speech_db" to voiceDb,
                                "snr_db" to (voiceDb - noiseDb), "clipped" to (peak >= 32_000),
                                "speech_ms" to (speechMs - silentMs), "vad" to (vad != null), "mode" to "talk",
                                "live" to true, "ended_by" to if (over) "server" else "phone")
                            // The server stayed silent past the limit: it hears the turn the old way from what it has.
                            if (!over) {
                                if (sendLive("end", turn)) main.post { onSent() }
                                else main.post { onProblem("Not connected to your server yet.") }
                            }
                        } else {
                            sendLive("cancel", turn)
                            Diag.event("clip", "dropped", "reason" to "too_short", "mic" to mic, "speech_ms" to (speechMs - silentMs))
                        }
                        chunk.reset(); chunkFrames = 0; chunkSpeech = false
                        streaming = false
                        speaking = false
                    }
                    continue
                }
                if (silentMs >= endSilenceMs() || speechMs >= MAX_UTTERANCE_MS) {
                    val voiceDb = Diag.db(if (speechFrames > 0) sqrt(speechSq / speechFrames) else rms)
                    val noiseDb = Diag.db(noiseAtStart)
                    val mic = if (earbudMic()) "headset" else "phone"
                    if (speechMs - silentMs >= MIN_SPEECH_MS) {
                        Diag.event("clip", "sent", "mic" to mic, "noise_db" to noiseDb, "speech_db" to voiceDb,
                            "snr_db" to (voiceDb - noiseDb), "clipped" to (peak >= 32_000), "speech_ms" to (speechMs - silentMs),
                            "vad" to (vad != null), "mode" to if (listen) "listen" else "talk")
                        sendUtterance(utterance.toByteArray())
                    } else {
                        Diag.event("clip", "dropped", "reason" to "too_short", "mic" to mic, "speech_ms" to (speechMs - silentMs))
                    }
                    utterance.reset()
                    speaking = false
                }
            }
        } finally {
            if (speaking && streaming) sendLive("cancel", turn)  // stopped mid-turn: nothing to answer
            runCatching { vad?.close() }
            runCatching { recorder?.stop() }
            recorder?.release()
            runCatching { cleaning?.release() }
            cleaning = null
            runCatching { boost?.release() }
            boost = null
        }
    }

    private fun sendUtterance(pcm: ByteArray) {
        val message = JSONObject()
            .put("type", "audio")
            .put("pcm", Base64.encodeToString(pcm, Base64.NO_WRAP))
            .put("lang", language)
            // Talk mode is you talking to Buddy: the English tutor may learn from it (room speech isn't).
            .put("mode", if (BuddyState.ui.value.mode == Mode.TALK) "talk" else "listen")
            // For Insights' comparison: did the phone's noise suppressor clean this clip?
            .put("ns", cleaning?.enabled == true)
            .put("mic", if (earbudMic()) "headset" else "phone")  // which mic heard it, for Insights
        if (!send(message)) main.post { onProblem("Not connected to your server yet.") }
        else main.post { onSent() }
    }

    /** One step of a live turn: its start (with what was heard before it), more audio, its end, or dropping it. */
    private fun sendLive(op: String, id: String, pcm: ByteArray? = null, speech: Boolean = true): Boolean {
        val message = JSONObject().put("type", "live").put("op", op).put("id", id)
        if (pcm != null) message.put("pcm", Base64.encodeToString(pcm, Base64.NO_WRAP)).put("sp", speech)
        if (op == "start") message.put("lang", language).put("ns", cleaning?.enabled == true)
            .put("mic", if (earbudMic()) "headset" else "phone")
        return send(message)
    }

    private fun toBytes(frame: ShortArray, n: Int): ByteArray {
        val buffer = ByteBuffer.allocate(n * 2).order(ByteOrder.LITTLE_ENDIAN)
        buffer.asShortBuffer().put(frame, 0, n)
        return buffer.array()
    }

    private companion object {
        const val RATE = 16_000
        const val FRAME = SileroVad.FRAME  // 512 samples: the speech model's frame
        const val FRAME_MS = FRAME * 1000 / RATE  // 32 ms
        const val PRE_ROLL_FRAMES = 10        // 320 ms before the first speech frame
        const val MIN_SPEECH_MS = 300  // a quick "Buddy" is about this long
        const val MAX_UTTERANCE_MS = 15_000
        const val LIVE_CHUNK_FRAMES = 3    // live hearing: about a tenth of a second goes out at a time
        const val LIVE_MAX_MS = 45_000     // a live turn can be longer than a clip: nothing waits for its end
    }
}
