package app.buddy

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
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

class Ears(
    private val context: Context,
    private val onHeard: (text: String, final: Boolean) -> Unit,
    private val onActive: (Boolean) -> Unit,
    private val onProblem: (String) -> Unit,
) : Hearing {
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var language = "en-US"
    private var autoLanguage = false
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
        autoLanguage = lang == AUTO_LANGUAGE
        language = if (autoLanguage) Locale.getDefault().toLanguageTag() else lang
        detected = null
        failures = 0
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
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, currentLanguage)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            if (autoLanguage && Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                // Android 14+: the recognizer works out the language and follows switches.
                val languages = ArrayList(DETECTABLE_LANGUAGES)
                putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_DETECTION, true)
                putStringArrayListExtra(RecognizerIntent.EXTRA_LANGUAGE_DETECTION_ALLOWED_LANGUAGES, languages)
                putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_SWITCH, RecognizerIntent.LANGUAGE_SWITCH_BALANCED)
                putStringArrayListExtra(RecognizerIntent.EXTRA_LANGUAGE_SWITCH_ALLOWED_LANGUAGES, languages)
            }
            // "Pauses when I talk" (Settings): how long a silence means you've finished.
            val silence = BuddyPrefs(context).endSilenceMs
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, silence + 500)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, silence)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && Vocabulary.words.isNotEmpty()) {
                putStringArrayListExtra(RecognizerIntent.EXTRA_BIASING_STRINGS, ArrayList(Vocabulary.words.take(50)))
            }
        }
        runCatching { recognizer?.startListening(intent) }.onFailure { restartSoon() }
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
                } else {
                    onProblem("Speech recognition doesn't support $language on this phone yet.")
                    stop()
                }
                SpeechRecognizer.ERROR_NO_MATCH,
                SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> restartSoon() // just quiet
                else -> {
                    failures++
                    recognizer?.cancel()
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

class Mouth(context: Context, private val onSpeaking: (Boolean) -> Unit) : TextToSpeech.OnInitListener {
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

    private fun applyVoice(name: String) {
        val voice = runCatching { tts.voices }.getOrNull()?.firstOrNull { it.name == name }
        if (voice != null) tts.voice = voice else tts.language = Locale.US
    }

    /** Queue one sentence. Quiet = a Listen-in whisper: softer and a little faster. */
    fun speak(text: String, quiet: Boolean = false) {
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
