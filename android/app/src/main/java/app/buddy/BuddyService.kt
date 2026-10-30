package app.buddy

import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.IBinder
import android.util.Log
import org.json.JSONObject
import java.util.Locale

/**
 * Buddy on the phone. A foreground service, so it keeps listening and stays
 * connected with the screen off (Android shows a notification while it runs).
 * The phone is only ears and mouth: all thinking, memory and agents live on
 * your server.
 */
/** Earbuds and headphones: plugging one in can start Buddy. */
private val HEADSET_TYPES = setOf(
    AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
    AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
    AudioDeviceInfo.TYPE_BLE_HEADSET,
    AudioDeviceInfo.TYPE_WIRED_HEADSET,
    AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
    AudioDeviceInfo.TYPE_USB_HEADSET,
)

/** Malayalam nicknames you can call Buddy by, as speech recognition writes them in English letters. */
private const val NICKNAMES = "aliya|aliyaa|aliyo|aliya|alia|aliyane|machaa|macha|machane|machan|machambi"

/** "Hey Buddy, ...", "Buddy, ...", also as speech recognition often hears it ("hey body",
 *  "hey bud", "okay buddy"), after a filler word ("so hey buddy..."). */
private val WAKE_PHRASE = Regex(
    "^(?:(?:so|okay|ok|um|uh|and|well|yeah)[\\s,]+)?" +
        "(?:(?:hey|hi|hello|ok|okay|yo|eda|da)[\\s,]+(?:buddy|buddie|body|bodhi|birdie|bud|baddie|bro|brother|$NICKNAMES)" +
        "|buddy|buddie|bro|$NICKNAMES)" +
        "\\b[\\s,.!?]*(.*)$",
    RegexOption.IGNORE_CASE,
)

/** "..., buddy?" at the end: "can you hear me buddy". */
private val WAKE_AT_END = Regex("^(.+?)[\\s,]+(?:hey\\s+)?(?:buddy|buddie|bro|$NICKNAMES)[.!?]*$", RegexOption.IGNORE_CASE)

/** The same in Malayalam script, as the recogniser writes it in Malayalam mode: ബഡി, ബ്രോ, അളിയാ, മച്ചാ.
 *  (No \b here: Java's word boundaries don't understand Malayalam letters.) */
private const val ML_NAMES = "ബഡി|ബഡ്ഡി|ബ്രോ|അളിയാ|അളിയ|അളിയോ|അളിയനേ|മച്ചാ|മച്ചാനെ|മച്ചാനേ|മച്ചമ്പി"
private val WAKE_ML = Regex("^(?:(?:ഹേയ്|ഹായ്|എടാ|ഡാ)[\\s,]*)?(?:$ML_NAMES)[\\s,.!?]*(.*)$")
private val WAKE_ML_AT_END = Regex("^(.+?)[\\s,]+(?:$ML_NAMES)[\\s.!?]*$")

/** "Are you listening?", "you there?", "can you hear me?": a check-in, like calling its name. */
private val CHECK_IN = Regex(
    "^(?:(?:hey\\s+)?(?:buddy|bro|$NICKNAMES|$ML_NAMES)[\\s,]+)?(?:are you (?:listening|there|awake)(?: to me)?|you there|" +
        "can you hear me|are you with me|കേൾക്കുന്നുണ്ടോ|കേക്കുന്നുണ്ടോ|അവിടെ ഉണ്ടോ|ഉണ്ടോ അവിടെ|" +
        "kelkkunnundo|kekkunnundo|avide undo)(?:[\\s,]+(?:buddy|bro|$NICKNAMES|$ML_NAMES))?[\\s?.!]*$",
    RegexOption.IGNORE_CASE,
)

class BuddyService : Service() {

    companion object {
        const val ACTION_START = "app.buddy.START"
        const val ACTION_STOP = "app.buddy.STOP"
        const val ACTION_TOGGLE = "app.buddy.TOGGLE"
        const val ACTION_APPROVE = "app.buddy.APPROVE"
        const val ACTION_DECLINE = "app.buddy.DECLINE"
        private const val TAG = "BuddyService"

        /** The running service, for the screen's buttons. Null when Buddy is stopped. */
        var instance: BuddyService? = null
            private set

        fun start(context: Context) {
            context.startForegroundService(
                Intent(context, BuddyService::class.java).setAction(ACTION_START)
            )
        }
    }

    private lateinit var prefs: BuddyPrefs
    @Volatile private var screenOff = false
    @Volatile private var whisperOnServer = false

    /** Screen off (phone closed or locked) or on: Automatic speech switches ears. */
    private val screenWatch = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            screenOff = intent.action == Intent.ACTION_SCREEN_OFF
            if (prefs.speechEngine == "auto" && whisperOnServer && (phoneEars.active || serverEars.active)) {
                startListening(activeMode)  // stops the current ears and starts the right ones
            }
        }
    }

    /** Does the server have Whisper? Asked on every connect. */
    private fun checkWhisper(link: ServerLink) {
        Thread {
            whisperOnServer = runCatching {
                okhttp3.OkHttpClient().newCall(okhttp3.Request.Builder().url(link.baseUrl + "/health").build())
                    .execute().use { JSONObject(it.body?.string() ?: "{}").optBoolean("whisper") }
            }.getOrDefault(false)
        }.start()
    }

    private lateinit var phoneEars: Ears
    private lateinit var serverEars: ServerEars

    /** The ears in use: whichever is listening, else the one chosen in Settings. */
    private val ears: Hearing
        get() = when {
            serverEars.active -> serverEars
            phoneEars.active -> phoneEars
            prefs.speechEngine == "server" -> serverEars
            // Automatic: Google's recognizer while the screen is on; with the phone closed or
            // locked it often stops, so Whisper on your server takes over (if it's installed).
            prefs.speechEngine == "auto" && screenOff && whisperOnServer -> serverEars
            else -> phoneEars
        }
    private lateinit var mouth: Mouth
    private lateinit var calls: CallWatcher
    private lateinit var button: EarbudButton
    private var client: BuddyClient? = null
    private var foreground = false

    private var activeMode = Mode.TALK // the mode the microphone was started in
    private var replyOpen = false      // Buddy's current reply is still arriving
    private var muted = false          // after an interrupt, drop the rest of the old reply
    private var resumeAfterCall: Mode? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        prefs = BuddyPrefs(this)
        registerReceiver(screenWatch, android.content.IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SCREEN_ON)
        })
        Notes.createChannels(this)
        val onActive = { on: Boolean -> BuddyState.update { it.copy(listening = on) }; refreshNotification() }
        val onProblem = { problem: String -> BuddyState.add(Line(problem, Speaker.ERROR)) }
        if (prefs.beepMuted) {  // the app stopped while the listening beep was muted: give sound back
            val audio = getSystemService(AudioManager::class.java)
            for (stream in intArrayOf(AudioManager.STREAM_MUSIC, AudioManager.STREAM_SYSTEM)) {
                runCatching { audio.adjustStreamVolume(stream, AudioManager.ADJUST_UNMUTE, 0) }
            }
            prefs.beepMuted = false
        }
        phoneEars = Ears(this, onHeard = ::heard, onActive = onActive, onProblem = onProblem,
            speaking = { this::mouth.isInitialized && mouth.speaking },
            paused = { this::mouth.isInitialized && mouth.speaking && !headsetConnected() })
        serverEars = ServerEars(
            earbudMic = { earbudMicOn },
            send = { message -> client?.send(message) ?: false },
            onHeard = ::heard, onActive = onActive, onProblem = onProblem,
            // On the loudspeaker Buddy would hear itself; with earbuds that's no problem.
            paused = { mouth.speaking && activeMode == Mode.TALK && !headsetConnected() },
            endSilenceMs = { BuddyPrefs(this).endSilenceMs - 200 },
        )
        mouth = Mouth(this, onMissingVoice = { name ->
            BuddyState.add(Line("To hear me in $name, add its voice: phone Settings → Text-to-speech → " +
                "Google → Install voice data → $name.", Speaker.ERROR))
        }) { on ->
            if (on) {
                phoneEars.releaseMute()  // before the first word plays
                if (!headsetConnected()) phoneEars.hold()  // on the loudspeaker it would hear itself
            } else {
                speechEndedAt = android.os.SystemClock.elapsedRealtime()
            }
            BuddyState.update { it.copy(speaking = on) }
            // Buddy's voice plays through the speech engine, which Android counts as another
            // app: claim the earbud button back after every reply.
            if (!on) {
                claimEarbud()
                // Just answered something asked with "Hey Buddy": a follow-up needs no wake phrase.
                if (answeringRoom) openConversation()
            }
        }
        calls = CallWatcher(this, ::callChanged)
        button = EarbudButton(this) { toggle() }
        calls.start()
        // Reports the earbuds already connected straight away, then every change.
        getSystemService(AudioManager::class.java).registerAudioDeviceCallback(headsets, main)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!foreground && !goForeground()) return START_NOT_STICKY
        when (intent?.action) {
            ACTION_STOP -> {
                shutDown()
                return START_NOT_STICKY
            }
            ACTION_TOGGLE -> toggle()
            ACTION_APPROVE -> answerApproval(true)
            ACTION_DECLINE -> answerApproval(false)
        }
        connect()
        button.claim()
        // Not sticky: Android can't restart a microphone service from the background.
        return START_NOT_STICKY
    }

    fun voices(): List<VoiceChoice> = if (::mouth.isInitialized) mouth.voices() else emptyList()

    fun useVoice(name: String) {
        if (::mouth.isInitialized) main.post { mouth.useVoice(name, "Hey, it's Buddy. How do I sound?") }
    }

    /** Make Buddy the app that receives the earbud's play/pause tap. */
    fun claimEarbud() {
        if (::button.isInitialized) main.post { button.claim() }
    }

    override fun onDestroy() {
        runCatching { unregisterReceiver(screenWatch) }
        phoneEars.destroy()
        serverEars.destroy()
        watchdog.removeCallbacksAndMessages(null)
        tone?.release()
        mouth.shutdown()
        calls.stop()
        getSystemService(AudioManager::class.java).unregisterAudioDeviceCallback(headsets)
        stopListenMic()
        button.release()
        calendarTimer.removeCallbacksAndMessages(null)
        useEarbudMic(false)
        client?.close()
        client = null
        instance = null
        BuddyState.update { it.copy(running = false, online = false, listening = false, speaking = false) }
        super.onDestroy()
    }

    // --- connection ------------------------------------------------------

    private fun connect() {
        if (client != null) return
        val link = prefs.link ?: run {
            BuddyState.update { it.copy(connection = "not set up") }
            return
        }
        client = BuddyClient(link, ::onServer) { status ->
            BuddyState.update {
                it.copy(
                    online = status == BuddyClient.Status.ONLINE,
                    connection = when (status) {
                        BuddyClient.Status.CONNECTING -> "connecting…"
                        BuddyClient.Status.ONLINE -> "online"
                        BuddyClient.Status.OFFLINE -> "offline — retrying"
                        BuddyClient.Status.BAD_TOKEN -> "wrong token — check the server link"
                    },
                )
            }
            refreshNotification()
            if (status == BuddyClient.Status.ONLINE) {
                checkWhisper(link)
                PhoneBridge.flush()  // notifications that arrived while offline
                syncCalendar()
                LocationShare.maybeSend(this, force = true)
            }
        }.also { it.connect() }
        scheduleCalendarSync()
    }

    /** For the phone bridge: send without telling the user if offline. */
    fun sendQuietly(message: JSONObject): Boolean = client?.send(message) ?: false

    /** Settings → voice speed: hear the new speed straight away. */
    fun previewVoice() {
        mouth.stop()
        mouth.speak("This is how fast I'll talk.")
    }

    /** Thumbs on a reply (null clears): shown per model and purpose in Insights. */
    fun rate(turn: String, rating: Int?) {
        val message = JSONObject().put("type", "rate").put("turn", turn)
            .put("up", if (rating == null) JSONObject.NULL else rating > 0)
        if (sendQuietly(message)) BuddyState.rated(turn, rating)
        else BuddyState.add(Line("Not connected, so the rating wasn't saved.", Speaker.ERROR))
    }

    private val calendarTimer = android.os.Handler(android.os.Looper.getMainLooper())
    private val main = android.os.Handler(android.os.Looper.getMainLooper())

    private fun scheduleCalendarSync() {
        calendarTimer.removeCallbacksAndMessages(null)
        calendarTimer.postDelayed({
            syncCalendar()
            scheduleCalendarSync()
        }, 30 * 60 * 1000L)
    }

    /** Send this phone's next week of calendar events, if the user turned it on. */
    fun syncCalendar() {
        if (!prefs.shareCalendar) return
        Thread {
            val message = runCatching { PhoneBridge.calendar(this) }.getOrNull() ?: return@Thread
            PhoneBridge.send(message)
        }.start()
    }

    /** After the server link changes. */
    fun reconnect() {
        client?.close()
        client = null
        connect()
    }

    private fun send(message: JSONObject): Boolean {
        val sent = client?.send(message) ?: false
        if (!sent) BuddyState.add(Line("Not connected to your server yet.", Speaker.ERROR))
        return sent
    }

    // --- "got it": a soft tone when the question arrives, and no silent waits ---------------
    private val tone by lazy { runCatching { android.media.ToneGenerator(AudioManager.STREAM_MUSIC, 35) }.getOrNull() }
    private val watchdog = android.os.Handler(android.os.Looper.getMainLooper())

    private fun thinking(on: Boolean) {
        watchdog.removeCallbacksAndMessages(null)
        BuddyState.update { it.copy(thinking = on) }
        if (!on) return
        phoneEars.releaseMute()
        tone?.startTone(android.media.ToneGenerator.TONE_PROP_ACK, 120)
        watchdog.postDelayed({ mouth.speak("Still working on it.", quiet = true) }, 20_000)
        watchdog.postDelayed({
            BuddyState.update { it.copy(thinking = false) }
            mouth.speak("Sorry, I lost that one. Can you ask again?")
        }, 75_000)
    }

    private fun onServer(event: ServerEvent) {
        if (event !is ServerEvent.Thinking && event !is ServerEvent.Heard && BuddyState.ui.value.thinking) {
            thinking(false)  // the answer (or an error, or a question) has started
        }
        when (event) {
            is ServerEvent.Say -> if (!muted) {
                if (event.filler) {
                    BuddyState.status(event.text)
                } else {
                    BuddyState.buddySentence(event.display ?: event.text, newReply = !replyOpen)
                    replyOpen = true
                }
                mouth.speak(event.text)
            }
            is ServerEvent.Cards -> BuddyState.attachCards(event.json)
            is ServerEvent.Words -> Vocabulary.words = event.words
            is ServerEvent.Done -> {
                replyOpen = false
                BuddyState.replyDone(event.turn)
            }
            ServerEvent.Thinking -> thinking(true)
            is ServerEvent.Heard -> serverEars.heard(event.text, event.lang)
            is ServerEvent.Approval -> {
                BuddyState.update { it.copy(approval = event.question) }
                Notes.approval(this, event.question)
            }
            is ServerEvent.Nudge -> {
                BuddyState.add(Line(event.text, Speaker.NUDGE, event.jobId, opensInsights = event.kind == "health",
                    kind = event.kind, cards = event.cards))
                Notes.nudge(this, event.text, event.jobId)
                if (!BuddyState.ui.value.onCall) mouth.speak(event.text)
            }
            is ServerEvent.Whisper -> {
                val label = when (event.kind) {
                    "translation" -> "Translation: "
                    "about_you" -> "About you: "
                    "correction" -> "Fact check: "
                    "answer" -> "Say this: "
                    else -> ""
                }
                // After a "let me think" answer, a follow-up question needs no "Hey Buddy".
                if (event.kind == "answer") answeringRoom = true
                BuddyState.add(Line(label + event.text, Speaker.WHISPER))
                mouth.speak(event.text, quiet = true)
            }
            is ServerEvent.Error -> {
                BuddyState.add(Line(event.text, Speaker.ERROR))
                mouth.speak(event.text)
            }
        }
    }

    // --- talking and listening -------------------------------------------

    /** Approve or Decline: answers Buddy's question for you, like saying it. */
    fun answerApproval(yes: Boolean) {
        if (BuddyState.ui.value.approval == null) return
        sayToBuddy(if (yes) "Yes, go ahead." else "No, don't do it.")
    }

    /** Something you said or typed, for Buddy. via "room": heard through Listen-in ("Hey Buddy, ..."),
     *  where anyone nearby could have said it, so the server won't take it as an approval. */
    fun sayToBuddy(text: String, via: String = "user") {
        if (via != "room") answeringRoom = false
        if (BuddyState.ui.value.approval != null) {  // any answer settles the question
            BuddyState.update { it.copy(approval = null) }
            Notes.clearApproval(this)
        }
        mouth.stop()
        muted = false
        replyOpen = false
        LocationShare.maybeSend(this)  // "near me" should mean where you are now
        BuddyState.add(Line(text, Speaker.YOU))
        if (!send(JSONObject().put("type", "user").put("text", text).put("via", via))) {
            mouth.speak("I can't reach your server right now. Check Tailscale, then say it again.")
        }
    }

    /** You started talking over Buddy: stop speaking and drop the rest of that reply. */
    fun interrupt() {
        mouth.stop()
        muted = true
        replyOpen = false
        client?.send(JSONObject().put("type", "interrupt"))
    }

    fun startListening(mode: Mode) {
        if (BuddyState.ui.value.onCall) {
            BuddyState.add(Line("You're on a call. I'll be back when it ends.", Speaker.ERROR))
            return
        }
        if (ears.active) stopListening()
        activeMode = mode
        BuddyState.update { it.copy(mode = mode) }
        if (mode == Mode.TALK) useEarbudMic(prefs.earbudMic) else startListenMic()
        val language = if (mode == Mode.LISTEN_IN) prefs.roomLanguage
            else prefs.talkLanguage.ifEmpty { Locale.getDefault().toLanguageTag() }
        ears.start(language)
    }

    fun stopListening() {
        if (ears.active && activeMode == Mode.LISTEN_IN) {
            client?.send(JSONObject().put("type", "ambient_stop"))
        }
        ears.stop()
        stopListenMic()
        useEarbudMic(false)
    }

    // --- Listen-in microphone: automatic follows the phone in and out of a pocket ---------
    private var inPocket = false
    private val proximity = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent) {
            val near = event.values[0] < event.sensor.maximumRange.coerceAtMost(5f)
            if (near == inPocket) return
            inPocket = near
            // In a pocket the phone's mic is muffled: hear the room through the earbuds instead.
            if (ears.active && activeMode == Mode.LISTEN_IN) useEarbudMic(inPocket)
        }

        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    }

    private fun startListenMic() {
        when (prefs.listenMic) {
            "earbuds" -> useEarbudMic(true)
            "phone" -> useEarbudMic(false)
            else -> {
                val sensors = getSystemService(SensorManager::class.java)
                val sensor = sensors?.getDefaultSensor(Sensor.TYPE_PROXIMITY)
                if (sensor == null) {
                    useEarbudMic(false)
                } else {
                    sensors?.registerListener(proximity, sensor, SensorManager.SENSOR_DELAY_NORMAL)
                    useEarbudMic(inPocket)
                }
            }
        }
    }

    private fun stopListenMic() {
        getSystemService(SensorManager::class.java)?.unregisterListener(proximity)
    }

    /** One button does the obvious thing: interrupt Buddy, or start/stop listening. */
    fun toggle() {
        when {
            mouth.speaking -> {
                interrupt()
                if (!ears.active) startListening(BuddyState.ui.value.mode)
            }
            ears.active -> stopListening()
            else -> startListening(BuddyState.ui.value.mode)
        }
    }

    // --- earbuds in: Buddy starts by itself -------------------------------------

    private var autoStarted = false
    private val headsets = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>) {
            if (addedDevices.any { it.isSink && it.type in HEADSET_TYPES }) earbudsIn()
        }

        override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>) {
            if (removedDevices.any { it.type in HEADSET_TYPES } && !headsetConnected()) earbudsOut()
        }
    }

    private fun headsetConnected(): Boolean =
        getSystemService(AudioManager::class.java).getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .any { it.type in HEADSET_TYPES }

    private fun earbudsIn() {
        if (ears.active || BuddyState.ui.value.onCall) return
        val mode = when (prefs.earbudsStart) {
            "listen" -> Mode.LISTEN_IN
            "talk" -> Mode.TALK
            else -> return
        }
        autoStarted = true
        startListening(mode)
        mouth.speak(if (mode == Mode.LISTEN_IN) "I'm here. Say hey Buddy when you need me." else "I'm listening.", quiet = true)
    }

    private fun earbudsOut() {
        if (autoStarted && ears.active) stopListening()
        autoStarted = false
        wakeArmed = false
    }

    // --- "Hey Buddy": talk to Buddy from Listen-in, no tapping ----------------

    private var wakeArmed = false
    private var answeringRoom = false   // Buddy's current reply answers a "Hey Buddy" request
    private var followUpUntil = 0L
    private val FOLLOW_UP_MS = 30_000L  // a natural pause to think before answering back

    /** The words after "Hey Buddy", "" for the wake phrase alone, or null if it wasn't said. */
    /** After an answer: the next 30 seconds need no "Hey Buddy"; the screen says so. */
    private fun openConversation() {
        followUpUntil = android.os.SystemClock.elapsedRealtime() + FOLLOW_UP_MS
        BuddyState.update { it.copy(conversationOpen = true) }
        main.removeCallbacks(closeConversation)
        main.postDelayed(closeConversation, FOLLOW_UP_MS)
    }

    private val closeConversation = Runnable {
        if (android.os.SystemClock.elapsedRealtime() >= followUpUntil) {
            BuddyState.update { it.copy(conversationOpen = false) }
        }
    }

    private fun afterWakePhrase(text: String): String? =
        WAKE_PHRASE.find(text.trim())?.groupValues?.get(1)?.trim()
            ?: WAKE_AT_END.find(text.trim())?.groupValues?.get(1)?.trim()
            ?: WAKE_ML.find(text.trim())?.groupValues?.get(1)?.trim()
            ?: WAKE_ML_AT_END.find(text.trim())?.groupValues?.get(1)?.trim()

    private var speechEndedAt = 0L

    /** Buddy's own voice coming back through the mic (loudspeaker): what it just said, heard again. */
    private fun ownEcho(text: String): Boolean {
        if (headsetConnected()) return false
        if (mouth.speaking) return true
        if (android.os.SystemClock.elapsedRealtime() - speechEndedAt > 2_500) return false
        val heardWords = words(text)
        if (heardWords.isEmpty()) return false
        val said = mouth.recentlySaid.flatMap { words(it) }.toSet()
        return heardWords.count { it in said } >= heardWords.size * 0.6
    }

    private fun words(text: String): List<String> =
        text.lowercase().split(Regex("[^\\p{L}\\p{N}']+")).filter { it.length > 1 }

    private fun heard(text: String, final: Boolean) {
        if (ownEcho(text)) return
        if (activeMode == Mode.LISTEN_IN && final) {
            val followUp = android.os.SystemClock.elapsedRealtime() < followUpUntil
            if (CHECK_IN.matches(text.trim())) {
                wakeArmed = true
                mouth.speak("Yes, I'm listening.", quiet = true)
                return
            }
            val request = if (wakeArmed || followUp) afterWakePhrase(text) ?: text else afterWakePhrase(text)
            when {
                request == null -> Unit
                request.isEmpty() -> { wakeArmed = true; mouth.speak("Yeah?", quiet = true); return }
                else -> {
                    wakeArmed = false
                    followUpUntil = 0L
                    BuddyState.update { it.copy(conversationOpen = false) }
                    answeringRoom = true
                    sayToBuddy(request, via = "room")
                    return
                }
            }
        }
        if (activeMode == Mode.LISTEN_IN) {
            // Other people talking: the server decides whether to whisper. Never barge in.
            if (final) {
                client?.send(
                    JSONObject().put("type", "ambient").put("text", text).put("lang", ears.currentLanguage)
                )
            }
            return
        }
        // Barge-in needs a few words, so a cough or Buddy's own voice doesn't trigger it.
        if (!final && mouth.speaking && text.split(Regex("\\s+")).size >= 3) interrupt()
        if (final) {
            sayToBuddy(text)
            if (!prefs.handsFree) stopListening()
        }
    }

    // --- calls -----------------------------------------------------------

    private fun callChanged(inCall: Boolean) {
        if (inCall == BuddyState.ui.value.onCall) return
        BuddyState.update { it.copy(onCall = inCall) }
        if (inCall) {
            resumeAfterCall = if (ears.active) activeMode else null
            stopListening()
            mouth.stop()
        } else {
            resumeAfterCall?.let { startListening(it) }
            resumeAfterCall = null
        }
        refreshNotification()
    }

    // --- audio routing ---------------------------------------------------

    /** Hear you through the earbud mic (Bluetooth headset mode). Off: the phone's mic. */
    /** True while the earbud microphone is the one listening. */
    @Volatile
    private var earbudMicOn = false

    private var earbudNoteAt = 0L

    private fun useEarbudMic(on: Boolean) {
        val audio = getSystemService(AudioManager::class.java)
        if (!on) {
            if (earbudMicOn || calls.buddyOwnsMode) useEarbudMicOff(audio)
            earbudMicOn = false
            return
        }
        if (earbudMicOn) return  // already on
        val device = audio.availableCommunicationDevices.firstOrNull {
            it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || it.type == AudioDeviceInfo.TYPE_BLE_HEADSET
        }
        if (device == null) {
            // Only worth saying with earbuds connected (their mic isn't available), and not every time.
            if (headsetConnected() && System.currentTimeMillis() - earbudNoteAt > 30 * 60_000L) {
                earbudNoteAt = System.currentTimeMillis()
                BuddyState.add(Line("Your earbuds' microphone isn't available, so I'm using the phone's.", Speaker.ERROR))
            }
            return
        }
        // Bluetooth earbud mics only work in voice-call mode: switch to it (our own
        // call detector knows it's us), then send the microphone to the earbuds.
        if (audio.mode != AudioManager.MODE_IN_COMMUNICATION) {
            calls.buddyOwnsMode = true
            audio.mode = AudioManager.MODE_IN_COMMUNICATION
        }
        earbudMicOn = audio.setCommunicationDevice(device)
        if (!earbudMicOn) {
            BuddyState.add(Line("The earbuds wouldn't share their microphone, so I'm using the phone's.", Speaker.ERROR))
            useEarbudMicOff(audio)
        }
    }

    private fun useEarbudMicOff(audio: AudioManager) {
        audio.clearCommunicationDevice()
        if (calls.buddyOwnsMode) {
            audio.mode = AudioManager.MODE_NORMAL
            calls.buddyOwnsMode = false
        }
    }

    // --- foreground notification -----------------------------------------

    private fun goForeground(): Boolean = try {
        startForeground(
            Notes.STATUS_ID,
            Notes.status(this, statusText()),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
        )
        foreground = true
        BuddyState.update { it.copy(running = true) }
        true
    } catch (e: Exception) {
        // Android only allows starting a microphone service while Buddy is on screen.
        Log.w(TAG, "Could not start in the foreground", e)
        stopSelf()
        false
    }

    private fun statusText(): String {
        val ui = BuddyState.ui.value
        return when {
            ui.onCall -> "Paused for your call"
            ui.listening && activeMode == Mode.LISTEN_IN -> "Listening to the room"
            ui.listening -> "Listening to you"
            ui.online -> "Connected. Tap your earbud to talk."
            else -> ui.connection.replaceFirstChar { it.uppercase() }
        }
    }

    private fun refreshNotification() {
        if (!foreground) return
        getSystemService(NotificationManager::class.java)
            .notify(Notes.STATUS_ID, Notes.status(this, statusText()))
    }

    private fun shutDown() {
        stopListening()
        mouth.stop()
        client?.close()
        client = null
        BuddyState.update { it.copy(online = false, connection = "stopped") }
        stopForeground(STOP_FOREGROUND_REMOVE)
        foreground = false
        stopSelf()
    }
}
