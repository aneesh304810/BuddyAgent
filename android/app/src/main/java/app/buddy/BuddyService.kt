package app.buddy

import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
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
class BuddyService : Service() {

    companion object {
        const val ACTION_START = "app.buddy.START"
        const val ACTION_STOP = "app.buddy.STOP"
        const val ACTION_TOGGLE = "app.buddy.TOGGLE"
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
    private lateinit var ears: Ears
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
        Notes.createChannels(this)
        ears = Ears(
            this,
            onHeard = ::heard,
            onActive = { on -> BuddyState.update { it.copy(listening = on) }; refreshNotification() },
            onProblem = { problem -> BuddyState.add(Line(problem, Speaker.ERROR)) },
        )
        mouth = Mouth(this) { on -> BuddyState.update { it.copy(speaking = on) } }
        calls = CallWatcher(this, ::callChanged)
        button = EarbudButton(this) { toggle() }
        calls.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!foreground && !goForeground()) return START_NOT_STICKY
        when (intent?.action) {
            ACTION_STOP -> {
                shutDown()
                return START_NOT_STICKY
            }
            ACTION_TOGGLE -> toggle()
        }
        connect()
        button.claim()
        // Not sticky: Android can't restart a microphone service from the background.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        ears.destroy()
        mouth.shutdown()
        calls.stop()
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
                PhoneBridge.flush()  // notifications that arrived while offline
                syncCalendar()
            }
        }.also { it.connect() }
        scheduleCalendarSync()
    }

    /** For the phone bridge: send without telling the user if offline. */
    fun sendQuietly(message: JSONObject): Boolean = client?.send(message) ?: false

    private val calendarTimer = android.os.Handler(android.os.Looper.getMainLooper())

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

    private fun onServer(event: ServerEvent) {
        when (event) {
            is ServerEvent.Say -> if (!muted) {
                BuddyState.buddySentence(event.text, newReply = !replyOpen)
                replyOpen = true
                mouth.speak(event.text)
            }
            ServerEvent.Done -> replyOpen = false
            is ServerEvent.Nudge -> {
                BuddyState.add(Line(event.text, Speaker.NUDGE, event.jobId))
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

    /** Something you said or typed, for Buddy. */
    fun sayToBuddy(text: String) {
        mouth.stop()
        muted = false
        replyOpen = false
        BuddyState.add(Line(text, Speaker.YOU))
        send(JSONObject().put("type", "user").put("text", text))
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
        useEarbudMic(mode == Mode.TALK && prefs.earbudMic)
        val language = if (mode == Mode.LISTEN_IN) prefs.roomLanguage else Locale.getDefault().toLanguageTag()
        ears.start(language)
    }

    fun stopListening() {
        if (ears.active && activeMode == Mode.LISTEN_IN) {
            client?.send(JSONObject().put("type", "ambient_stop"))
        }
        ears.stop()
        useEarbudMic(false)
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

    private fun heard(text: String, final: Boolean) {
        if (activeMode == Mode.LISTEN_IN) {
            // Other people talking: the server decides whether to whisper. Never barge in.
            if (final) {
                client?.send(
                    JSONObject().put("type", "ambient").put("text", text).put("lang", prefs.roomLanguage)
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
    private fun useEarbudMic(on: Boolean) {
        val audio = getSystemService(AudioManager::class.java)
        if (!on) {
            audio.clearCommunicationDevice()
            return
        }
        audio.availableCommunicationDevices
            .firstOrNull {
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || it.type == AudioDeviceInfo.TYPE_BLE_HEADSET
            }
            ?.let { audio.setCommunicationDevice(it) }
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
