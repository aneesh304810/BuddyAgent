package app.buddy

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

/**
 * Buddy's ears: Android speech recognition, restarted after every phrase so it
 * keeps listening. Partial results allow barge-in (talking over Buddy).
 */
class Ears(
    private val context: Context,
    private val onHeard: (text: String, final: Boolean) -> Unit,
    private val onActive: (Boolean) -> Unit,
    private val onProblem: (String) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var language = "en-US"
    private var failures = 0

    var active = false
        private set

    fun start(lang: String) {
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            onProblem("This phone has no speech recognition service. Install or update the Google app.")
            return
        }
        language = lang
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

    fun stop() {
        main.removeCallbacksAndMessages(null)
        recognizer?.cancel()
        if (active) {
            active = false
            onActive(false)
        }
    }

    fun destroy() {
        stop()
        recognizer?.destroy()
        recognizer = null
    }

    private fun listen() {
        if (!active) return
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1_500L)
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
                SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> {
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
class Mouth(context: Context, private val onSpeaking: (Boolean) -> Unit) : TextToSpeech.OnInitListener {
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
        tts.setSpeechRate(if (quiet) 1.15f else 1.05f)
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
