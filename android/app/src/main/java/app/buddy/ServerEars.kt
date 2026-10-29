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
 */
class ServerEars(
    private val send: (JSONObject) -> Boolean,
    private val onHeard: (text: String, final: Boolean) -> Unit,
    private val onActive: (Boolean) -> Unit,
    private val onProblem: (String) -> Unit,
    private val paused: () -> Boolean,   // e.g. while Buddy talks on the loudspeaker
) : Hearing {
    private val main = Handler(Looper.getMainLooper())
    private var thread: Thread? = null
    private var language = AUTO_LANGUAGE
    private var detected: String? = null

    @Volatile
    override var active = false
        private set

    override val currentLanguage: String
        get() = detected ?: language

    override fun start(lang: String) {
        if (active) return
        language = lang
        detected = null
        active = true
        onActive(true)
        thread = Thread(::record, "buddy-server-ears").also { it.start() }
    }

    override fun stop() {
        if (!active) return
        active = false
        thread = null
        onActive(false)
    }

    override fun destroy() = stop()

    /** The server's answer for one utterance (main thread). */
    fun heard(text: String, lang: String) {
        if (!active || text.isBlank()) return
        if (lang.isNotEmpty() && language == AUTO_LANGUAGE) detected = lang
        onHeard(text, true)
    }

    @SuppressLint("MissingPermission") // the service only starts after the microphone is allowed
    private fun record() {
        val minBuffer = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val recorder = runCatching {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minBuffer, RATE),
            )
        }.getOrNull()
        if (recorder == null || recorder.state != AudioRecord.STATE_INITIALIZED) {
            main.post { onProblem("Couldn't open the microphone."); stop() }
            return
        }
        recorder.startRecording()
        val frame = ShortArray(FRAME)
        val preRoll = ArrayDeque<ByteArray>()
        val utterance = ByteArrayOutputStream()
        var noise = 300.0
        var speaking = false
        var silentMs = 0
        var speechMs = 0
        try {
            while (active) {
                val n = recorder.read(frame, 0, frame.size)
                if (n <= 0) continue
                if (paused()) {  // drop what we hear while Buddy itself is talking
                    speaking = false; utterance.reset(); preRoll.clear()
                    continue
                }
                var sum = 0.0
                for (i in 0 until n) sum += frame[i].toDouble() * frame[i]
                val rms = sqrt(sum / n)
                val loud = rms > max(noise * 3, 500.0)
                val bytes = toBytes(frame, n)
                if (!speaking) {
                    if (loud) {
                        speaking = true
                        silentMs = 0
                        speechMs = 0
                        preRoll.forEach { utterance.write(it) }
                        preRoll.clear()
                        utterance.write(bytes)
                    } else {
                        noise = noise * 0.95 + rms * 0.05  // follow the room's background level
                        preRoll.addLast(bytes)
                        if (preRoll.size > PRE_ROLL_FRAMES) preRoll.removeFirst()
                    }
                    continue
                }
                utterance.write(bytes)
                speechMs += FRAME_MS
                silentMs = if (loud) 0 else silentMs + FRAME_MS
                if (silentMs >= END_SILENCE_MS || speechMs >= MAX_UTTERANCE_MS) {
                    if (speechMs - silentMs >= MIN_SPEECH_MS) sendUtterance(utterance.toByteArray())
                    utterance.reset()
                    speaking = false
                }
            }
        } finally {
            runCatching { recorder.stop() }
            recorder.release()
        }
    }

    private fun sendUtterance(pcm: ByteArray) {
        val message = JSONObject()
            .put("type", "audio")
            .put("pcm", Base64.encodeToString(pcm, Base64.NO_WRAP))
            .put("lang", language)
        if (!send(message)) main.post { onProblem("Not connected to your server yet.") }
    }

    private fun toBytes(frame: ShortArray, n: Int): ByteArray {
        val buffer = ByteBuffer.allocate(n * 2).order(ByteOrder.LITTLE_ENDIAN)
        buffer.asShortBuffer().put(frame, 0, n)
        return buffer.array()
    }

    private companion object {
        const val RATE = 16_000
        const val FRAME_MS = 30
        const val FRAME = RATE * FRAME_MS / 1000
        const val PRE_ROLL_FRAMES = 10        // 300 ms before the first loud frame
        const val END_SILENCE_MS = 800
        const val MIN_SPEECH_MS = 400
        const val MAX_UTTERANCE_MS = 15_000
    }
}
