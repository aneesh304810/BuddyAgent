package app.buddy

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.Icon
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent

/**
 * Notices when any call starts: phone, WhatsApp, Teams, Meet. Android gives
 * the microphone to the call anyway, so Buddy pauses instead of listening to
 * silence, and picks up again when the call ends. Needs no permission: it
 * watches the audio mode, not your call log.
 */
class CallWatcher(context: Context, private val onCall: (Boolean) -> Unit) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val executor = context.mainExecutor
    private val listener = AudioManager.OnModeChangedListener { mode -> onCall(isCall(mode)) }

    /** True while Buddy itself holds the voice-call audio mode (for the earbud mic). */
    @Volatile
    var buddyOwnsMode = false

    fun start() {
        audio.addOnModeChangedListener(executor, listener)
        onCall(isCall(audio.mode))
    }

    fun stop() {
        runCatching { audio.removeOnModeChangedListener(listener) }
    }

    private fun isCall(mode: Int) = when (mode) {
        AudioManager.MODE_IN_COMMUNICATION -> !buddyOwnsMode
        AudioManager.MODE_IN_CALL,
        AudioManager.MODE_RINGTONE,
        AudioManager.MODE_CALL_SCREENING -> true
        else -> false
    }
}

/**
 * The earbud tap (play/pause) talks to Buddy. Android sends media buttons to
 * the app that played media most recently, so `claim()` plays a moment of
 * silence to become that app. Playing music elsewhere takes the button back
 * until Buddy is opened again.
 */
class EarbudButton(context: Context, private val onPress: () -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private val buttons = setOf(
        KeyEvent.KEYCODE_HEADSETHOOK,
        KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
        KeyEvent.KEYCODE_MEDIA_PLAY,
        KeyEvent.KEYCODE_MEDIA_PAUSE,
    )

    private val session = MediaSession(context, "Buddy").apply {
        setCallback(object : MediaSession.Callback() {
            override fun onMediaButtonEvent(mediaButtonIntent: Intent): Boolean {
                @Suppress("DEPRECATION")
                val event = mediaButtonIntent.getParcelableExtra<KeyEvent>(Intent.EXTRA_KEY_EVENT)
                    ?: return false
                if (event.keyCode !in buttons) return false
                if (event.action == KeyEvent.ACTION_UP && event.repeatCount == 0) onPress()
                return true
            }
        })
        setState(PlaybackState.STATE_PAUSED)
        isActive = true
    }

    fun claim() {
        val rate = 16_000
        val samples = rate / 4 // a quarter second of silence
        val track = runCatching {
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setTransferMode(AudioTrack.MODE_STATIC)
                .setBufferSizeInBytes(samples * 2)
                .build()
        }.getOrNull() ?: return
        track.write(ShortArray(samples), 0, samples)
        session.setState(PlaybackState.STATE_PLAYING)
        track.play()
        main.postDelayed({
            track.release()
            session.setState(PlaybackState.STATE_PAUSED)
        }, 500)
    }

    fun release() {
        main.removeCallbacksAndMessages(null)
        session.release()
    }

    private fun MediaSession.setState(state: Int) {
        setPlaybackState(
            PlaybackState.Builder()
                .setActions(
                    PlaybackState.ACTION_PLAY_PAUSE or PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE
                )
                .setState(state, 0L, 1f)
                .build()
        )
    }
}

/** Buddy's notifications: a quiet ongoing one while running, loud ones for reminders. */
object Notes {
    const val STATUS_ID = 1
    private const val APPROVAL_ID = 2
    private const val STATUS = "buddy_status"
    private const val NUDGES = "buddy_nudges"
    private var nextId = 100

    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(STATUS, "Buddy is running", NotificationManager.IMPORTANCE_LOW)
        )
        manager.createNotificationChannel(
            NotificationChannel(NUDGES, "Reminders and nudges", NotificationManager.IMPORTANCE_HIGH)
        )
    }

    fun status(context: Context, text: String): Notification {
        val stop = PendingIntent.getService(
            context, 1,
            Intent(context, BuddyService::class.java).setAction(BuddyService.ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val talk = PendingIntent.getService(
            context, 2,
            Intent(context, BuddyService::class.java).setAction(BuddyService.ACTION_TOGGLE),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val icon = Icon.createWithResource(context, R.drawable.ic_stat_buddy)
        return Notification.Builder(context, STATUS)
            .setSmallIcon(R.drawable.ic_stat_buddy)
            .setContentTitle("Buddy")
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(openApp(context))
            .addAction(Notification.Action.Builder(icon, "Talk", talk).build())
            .addAction(Notification.Action.Builder(icon, "Stop Buddy", stop).build())
            .build()
    }

    fun nudge(context: Context, text: String, jobId: Int? = null) {
        if (context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) return
        context.getSystemService(NotificationManager::class.java).notify(
            nextId++,
            Notification.Builder(context, NUDGES)
                .setSmallIcon(R.drawable.ic_stat_buddy)
                .setContentTitle("Buddy")
                .setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setContentIntent(jobId?.let { openReport(context, it) } ?: openApp(context))
                .build(),
        )
    }

    /** "Send this email?": Approve and Decline right on the notification. */
    fun approval(context: Context, question: String) {
        if (context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) return
        fun action(label: String, what: String, code: Int) = Notification.Action.Builder(
            Icon.createWithResource(context, R.drawable.ic_stat_buddy), label,
            PendingIntent.getService(
                context, code, Intent(context, BuddyService::class.java).setAction(what),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        ).build()
        context.getSystemService(NotificationManager::class.java).notify(
            APPROVAL_ID,
            Notification.Builder(context, NUDGES)
                .setSmallIcon(R.drawable.ic_stat_buddy)
                .setContentTitle("Buddy needs your OK")
                .setContentText(question)
                .setStyle(Notification.BigTextStyle().bigText(question))
                .setContentIntent(openApp(context))
                .addAction(action("Approve", BuddyService.ACTION_APPROVE, 2001))
                .addAction(action("Decline", BuddyService.ACTION_DECLINE, 2002))
                .build(),
        )
    }

    fun clearApproval(context: Context) {
        context.getSystemService(NotificationManager::class.java).cancel(APPROVAL_ID)
    }

    /** Tapping a "research is ready" notification opens that report. */
    private fun openReport(context: Context, jobId: Int): PendingIntent = PendingIntent.getActivity(
        context, 1000 + jobId,
        Intent(context, MainActivity::class.java).putExtra(MainActivity.EXTRA_JOB_ID, jobId),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0,
        Intent(context, MainActivity::class.java),
        PendingIntent.FLAG_IMMUTABLE,
    )
}
