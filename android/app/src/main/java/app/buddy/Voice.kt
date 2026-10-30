package app.buddy

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import java.util.Locale

/** Room language "automatic": the recognizer detects it (Android 14+). */
const val AUTO_LANGUAGE = "auto"

/** Languages automatic mode listens for: the user's own, then the common ones around them. */
val DETECTABLE_LANGUAGES = listOf(
    "en-US", "hi-IN", "ml-IN", "ta-IN", "mr-IN", "es-419", "pt-BR", "kn-IN", "te-IN", "fr-FR",
)

/** Something that hears the user: the phone's recognizer, or Whisper on the server. */
interface Hearing {
    val active: Boolean
    val currentLanguage: String
    fun start(lang: String)
    fun stop()
    fun destroy()
}

/**
 * Buddy's ears: Android speech recognition, restarted after every phrase so it
 * keeps listening. Partial results allow barge-in (talking over Buddy).
 */
/** Names and words from Buddy (your memory, words you corrected): the recogniser leans towards them. */
object Vocabulary {
    @Volatile var words: List<String> = emptyList()
}

/** Errors that mean the recogniser itself is stuck or lost its service: replace it, don't retry it. */
private val JAMMED = setOf(SpeechRecognizer.ERROR_CLIENT, SpeechRecognizer.ERROR_RECOGNIZER_BUSY,
    SpeechRecognizer.ERROR_SERVER_DISCONNECTED)

class Ears(
    private val context: Context,
    private val onHeard: (text: String, final: Boolean) -> Unit,
    private val onActive: (Boolean) -> Unit,
    private val onProblem: (String) -> Unit,
    private val speaking: () -> Boolean = { false },
    // On the loudspeaker Buddy would hear itself: no listening while it talks.
    private val paused: () -> Boolean = { false },
) : Hearing {
    private val main = Handler(Looper.getMainLooper())
    // The recogniser beeps when a session starts and ends, and Buddy starts one after every phrase
    // and every few seconds of silence. While Buddy listens quietly, media and system sounds stay
    // muted; the mute lifts the moment Buddy speaks ([releaseMute]) or another app plays audio.
    private val audio = context.getSystemService(AudioManager::class.java)
    private var beepMuted = false
    private var systemStreamSafe = true  // false if muting system sounds turned the ringer off
    private var recognizer: SpeechRecognizer? = null
    private var language = "en-US"
    private var autoLanguage = false
    private var allowed: List<String> = DETECTABLE_LANGUAGES
    // After repeated errors, listen without language switching and vocabulary hints: some phones'
    // recognisers reject them (they need on-device recognition) and would otherwise fail silently.
    private var simple = false
    private var detected: String? = null
    private var failures = 0

    /** What is being transcribed: the chosen language, or (automatic) the last one heard. */
    override val currentLanguage: String
        get() = if (autoLanguage) detected ?: language else language

    override var active = false
        private set

    override fun start(lang: String) {
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            onProblem("This phone has no speech recognition service. Install or update the Google app.")
            return
        }
        // "auto" = any of DETECTABLE_LANGUAGES; "auto:ml-IN,en-IN" = switch between just these.
        autoLanguage = lang == AUTO_LANGUAGE || lang.startsWith("$AUTO_LANGUAGE:")
        allowed = lang.substringAfter(":", "").split(",").map { it.trim() }.filter { it.isNotEmpty() }
            .ifEmpty { DETECTABLE_LANGUAGES }
        language = if (!autoLanguage) lang else if (lang.contains(":")) allowed.first() else Locale.getDefault().toLanguageTag()
        detected = null
        failures = 0
        // Once this phone's recogniser has rejected language switching and name hints, don't try
        // them again on every start (each failed try is an error, a retry and a beep).
        simple = BuddyPrefs(context).simpleRecognition
        if (recognizer == null) {
            recognizer = SpeechRecognizer.createSpeechRecognizer(context).also {
                it.setRecognitionListener(listener)
            }
        }
        active = true
        onActive(true)
        listen()
    }

    override fun stop() {
        main.removeCallbacksAndMessages(null)
        recognizer?.cancel()
        muteBeep(false)
        if (active) {
            active = false
            onActive(false)
        }
    }

    override fun destroy() {
        stop()
        recognizer?.destroy()
        recognizer = null
    }

    private fun listen() {
        if (!active) return
        if (paused()) {  // Buddy is talking on the loudspeaker: try again shortly, without starting (or beeping)
            main.removeCallbacksAndMessages(null)
            main.postDelayed({ listen() }, 300)
            return
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, currentLanguage)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            if (autoLanguage && !simple && Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                // Android 14+: the recognizer works out the language and follows switches.
                val languages = ArrayList(allowed)
                putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_DETECTION, true)
                putStringArrayListExtra(RecognizerIntent.EXTRA_LANGUAGE_DETECTION_ALLOWED_LANGUAGES, languages)
                putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_SWITCH, RecognizerIntent.LANGUAGE_SWITCH_BALANCED)
                putStringArrayListExtra(RecognizerIntent.EXTRA_LANGUAGE_SWITCH_ALLOWED_LANGUAGES, languages)
            }
            // "Pauses when I talk" (Settings): how long a silence means you've finished.
            val silence = BuddyPrefs(context).endSilenceMs
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, silence + 500)
            if (!simple) putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, silence)
            if (!simple && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && Vocabulary.words.isNotEmpty()) {
                putStringArrayListExtra(RecognizerIntent.EXTRA_BIASING_STRINGS, ArrayList(Vocabulary.words.take(50)))
            }
        }
        // Someone else's audio (music, a video) gets its sound back; otherwise stay quiet.
        muteBeep(!audio.isMusicActive)
        runCatching { recognizer?.startListening(intent) }.onFailure { restartSoon() }
    }

    /** Buddy starts talking on the loudspeaker: drop the current session; [listen] waits until it's done. */
    fun hold() {
        if (!active) return
        recognizer?.cancel()
        main.removeCallbacksAndMessages(null)
        main.postDelayed({ listen() }, 300)
    }

    /** Buddy is about to talk (or play its "got it" tone): give media its sound back. */
    fun releaseMute() = muteBeep(false)

    /** A new recogniser: the old one is stuck (busy, client error) or lost its service. */
    private fun fresh() {
        runCatching { recognizer?.destroy() }
        recognizer = runCatching {
            SpeechRecognizer.createSpeechRecognizer(context).also { it.setRecognitionListener(listener) }
        }.getOrNull()
    }

    private fun errorName(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "network timeout"
        SpeechRecognizer.ERROR_NETWORK -> "network error"
        SpeechRecognizer.ERROR_AUDIO -> "microphone error"
        SpeechRecognizer.ERROR_SERVER -> "recognition service error"
        SpeechRecognizer.ERROR_CLIENT -> "client error"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "recogniser busy"
        SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> "recognition service disconnected"
        SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> "too many requests"
        SpeechRecognizer.ERROR_CANNOT_CHECK_SUPPORT -> "can't check language support"
        else -> "error $error"
    }

    private fun muteBeep(on: Boolean) {
        if (on == beepMuted) return
        if (on && (!BuddyPrefs(context).muteListenBeep || speaking())) return
        beepMuted = on
        BuddyPrefs(context).beepMuted = on  // undone at the next start if the app dies mid-mute
        val direction = if (on) AudioManager.ADJUST_MUTE else AudioManager.ADJUST_UNMUTE
        runCatching { audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, direction, 0) }
        if (systemStreamSafe) {
            // On some phones system sounds share the ringer's volume: if muting them silenced the
            // ringer, undo it and leave system sounds alone from then on.
            val ringer = audio.ringerMode
            runCatching { audio.adjustStreamVolume(AudioManager.STREAM_SYSTEM, direction, 0) }
            if (on && audio.ringerMode != ringer) {
                runCatching { audio.adjustStreamVolume(AudioManager.STREAM_SYSTEM, AudioManager.ADJUST_UNMUTE, 0) }
                runCatching { audio.ringerMode = ringer }
                systemStreamSafe = false
            }
        }
    }

    /** Silence and timeouts end a session; start the next one, backing off if errors repeat. */
    private fun restartSoon() {
        if (!active) return
        val delay = (250L shl failures.coerceAtMost(5)).coerceAtMost(5_000L)
        main.removeCallbacksAndMessages(null)
        main.postDelayed({ listen() }, delay)
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onEvent(eventType: Int, params: Bundle?) {}

        override fun onError(error: Int) {
            when (error) {
                SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> {
                    onProblem("Buddy needs microphone permission.")
                    stop()
                }
                SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED,
                SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> if (autoLanguage && detected != null) {
                    detected = null // a detected language this phone can't transcribe: back to the default
                    restartSoon()
                } else if (allowed.size > 1 && allowed.indexOf(language) < allowed.lastIndex) {
                    // e.g. Malayalam isn't available for recognition here: carry on in the next language.
                    val missing = Locale.forLanguageTag(language).displayLanguage
                    language = allowed[allowed.indexOf(language) + 1]
                    onProblem("Speech recognition can't do $missing on this phone yet, so I'm listening in " +
                        "${Locale.forLanguageTag(language).displayLanguage}. To add it: Google app → Settings → " +
                        "Voice → Offline speech recognition → $missing.")
                    restartSoon()
                } else {
                    onProblem("Speech recognition doesn't support $language on this phone yet. " +
                        "Settings → My languages, or try Speech engine → Server.")
                    stop()
                }
                SpeechRecognizer.ERROR_NO_MATCH,
                SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> restartSoon() // just quiet
                else -> {
                    failures++
                    recognizer?.cancel()
                    if (error in JAMMED) fresh()  // a stuck or disconnected recogniser: start over with a new one
                    if (failures == 2 && !simple && error !in JAMMED) {
                        simple = true  // drop language switching and vocabulary hints, then retry
                        BuddyPrefs(context).simpleRecognition = true  // and don't try them again
                        onProblem("Speech recognition didn't accept language switching here (${errorName(error)}), " +
                            "so I'm listening in ${Locale.forLanguageTag(language).displayLanguage} only. (Settings → " +
                            "Advanced phone recognition turned off.)")
                    } else if (failures == 6) {
                        onProblem("Speech recognition keeps failing (${errorName(error)}). Try Settings → Speech " +
                            "engine → Server, or restart Buddy.")
                    } else if (failures >= 12) {
                        onProblem("Stopped listening: speech recognition kept failing (error $error), and each retry " +
                            "beeps. Settings → Speech engine → Server works without it; tap Talk to try again.")
                        stop()
                        return
                    }
                    restartSoon()
                }
            }
        }

        override fun onResults(results: Bundle?) {
            failures = 0
            best(results)?.let { onHeard(it, true) }
            restartSoon()
        }

        override fun onLanguageDetection(results: Bundle) {
            val lang = results.getString(SpeechRecognizer.DETECTED_LANGUAGE) ?: return
            val level = results.getInt(SpeechRecognizer.LANGUAGE_DETECTION_CONFIDENCE_LEVEL)
            if (autoLanguage && level >= SpeechRecognizer.LANGUAGE_DETECTION_CONFIDENCE_LEVEL_CONFIDENT) {
                detected = lang
            }
        }

        override fun onPartialResults(partialResults: Bundle?) {
            best(partialResults)?.let { onHeard(it, false) }
        }
    }

    private fun best(bundle: Bundle?): String? =
        bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            ?.firstOrNull()
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
}

/** Buddy's mouth: text-to-speech, played wherever the phone's audio goes (your earbuds). */
/** One voice Buddy can speak with, as shown in Settings. */
data class VoiceChoice(val name: String, val label: String)

/** Unicode blocks of Indian scripts, and the voice language for each. */
private val SCRIPTS = mapOf(
    '\u0D00'..'\u0D7F' to "ml-IN",  // Malayalam
    '\u0900'..'\u097F' to "hi-IN",  // Devanagari (Hindi, Marathi)
    '\u0B80'..'\u0BFF' to "ta-IN",  // Tamil
    '\u0C00'..'\u0C7F' to "te-IN",  // Telugu
    '\u0C80'..'\u0CFF' to "kn-IN",  // Kannada
    '\u0980'..'\u09FF' to "bn-IN",  // Bengali
    '\u0A80'..'\u0AFF' to "gu-IN",  // Gujarati
)

class Mouth(
    context: Context,
    private val onMissingVoice: (String) -> Unit = {},
    private val onSpeaking: (Boolean) -> Unit,
) : TextToSpeech.OnInitListener {
    private val prefs = BuddyPrefs(context)
    private val main = Handler(Looper.getMainLooper())
    private val tts = TextToSpeech(context.applicationContext, this)
    private val waiting = mutableListOf<Pair<String, Boolean>>()
    private val playing = mutableSetOf<String>()
    private var ready = false
    private var counter = 0

    val speaking: Boolean
        get() = playing.isNotEmpty()

    override fun onInit(status: Int) {
        main.post {
            if (status != TextToSpeech.SUCCESS) return@post
            tts.language = Locale.US
            applyVoice(prefs.voiceName)
            tts.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}

                override fun onDone(utteranceId: String?) {
                    finished(utteranceId)
                }

                @Deprecated("Required override")
                override fun onError(utteranceId: String?) {
                    finished(utteranceId)
                }

                override fun onStop(utteranceId: String?, interrupted: Boolean) {
                    finished(utteranceId)
                }
            })
            ready = true
            waiting.forEach { (text, quiet) -> speak(text, quiet) }
            waiting.clear()
        }
    }

    /** English voices installed on this phone (or downloadable by the engine), best first. */
    fun voices(): List<VoiceChoice> {
        if (!ready) return emptyList()
        val all = runCatching { tts.voices }.getOrNull().orEmpty()
            .filter { it.locale.language == "en" }
            .filterNot { TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED in it.features }
        // Google lists most voices twice (on the phone, and online): keep one of each.
        return all.groupBy { it.name.removeSuffix("-local").removeSuffix("-network") }
            .map { (_, same) -> same.minByOrNull { if (it.isNetworkConnectionRequired) 1 else 0 }!! }
            .sortedWith(compareBy<Voice>({ it.locale.country != "US" }, { it.locale.country }, { it.name }))
            .mapIndexed { i, v ->
                val place = v.locale.displayCountry.ifEmpty { "English" }
                val online = if (v.isNetworkConnectionRequired) ", needs internet" else ""
                VoiceChoice(v.name, "Voice ${i + 1} · $place$online")
            }
    }

    /** Switch voice ("" = the phone's default) and say a line so the user can hear it. */
    fun useVoice(name: String, preview: String? = null) {
        prefs.voiceName = name
        if (!ready) return
        applyVoice(name)
        preview?.let { stop(); speak(it) }
    }

    private var speakingScript = ""
    private var warnedMissing = mutableSetOf<String>()

    /** Pick the voice for a sentence by its script: mixed-language replies sound right. */
    private fun speakIn(text: String) {
        val script = SCRIPTS.entries.firstOrNull { (range, _) -> text.any { it in range } }?.value ?: ""
        if (script == speakingScript) return
        speakingScript = script
        if (script.isEmpty()) {
            applyVoice(prefs.voiceName)
            return
        }
        val result = tts.setLanguage(Locale.forLanguageTag(script))
        if ((result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) &&
            warnedMissing.add(script)) {
            onMissingVoice(Locale.forLanguageTag(script).displayLanguage)
        }
    }

    private fun applyVoice(name: String) {
        val voice = runCatching { tts.voices }.getOrNull()?.firstOrNull { it.name == name }
        if (voice != null) tts.voice = voice else tts.language = Locale.US
    }

    /** Queue one sentence. Quiet = a Listen-in whisper: softer and a little faster. */
    /** The last few things Buddy said, to recognise its own voice coming back through the mic. */
    val recentlySaid = ArrayDeque<String>()

    fun speak(text: String, quiet: Boolean = false) {
        recentlySaid.addLast(text)
        while (recentlySaid.size > 4) recentlySaid.removeFirst()
        if (!ready) {
            waiting += text to quiet
            return
        }
        val id = "u${counter++}"
        if (playing.isEmpty()) onSpeaking(true)
        playing += id
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, if (quiet) 0.6f else 1f)
        }
        speakIn(text)  // a Malayalam sentence in a Malayalam voice, an English one in yours
        val rate = prefs.voiceRate  // Settings → Voice speed
        tts.setSpeechRate(if (quiet) rate * 1.1f else rate)
        tts.speak(text, TextToSpeech.QUEUE_ADD, params, id)
    }

    fun stop() {
        waiting.clear()
        tts.stop()
        if (playing.isNotEmpty()) {
            playing.clear()
            onSpeaking(false)
        }
    }

    fun shutdown() {
        stop()
        tts.shutdown()
    }

    private fun finished(id: String?) {
        main.post {
            if (id != null && playing.remove(id) && playing.isEmpty()) onSpeaking(false)
        }
    }
}
