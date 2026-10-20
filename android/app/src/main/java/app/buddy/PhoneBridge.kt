package app.buddy

import android.Manifest
import android.app.Notification
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.provider.CalendarContract
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

/**
 * What this phone shares with your Buddy server: its notifications as they
 * arrive, and its calendar (a work calendar synced by Outlook or Teams included).
 * Every phone running Buddy does this, labelled with its own name, so a second
 * (work) phone can feed the same Buddy.
 */
object PhoneBridge {
    private const val MAX_QUEUE = 100
    private val queue = ArrayDeque<JSONObject>()

    /** Send now if Buddy is connected, otherwise keep it until it is. */
    @Synchronized
    fun send(message: JSONObject) {
        if (BuddyService.instance?.sendQuietly(message) == true) return
        if (queue.size >= MAX_QUEUE) queue.removeFirst()
        queue.addLast(message)
    }

    @Synchronized
    fun flush() {
        while (queue.isNotEmpty()) {
            if (BuddyService.instance?.sendQuietly(queue.first()) != true) return
            queue.removeFirst()
        }
    }

    fun deviceName(context: Context): String =
        BuddyPrefs(context).deviceName.ifBlank { Build.MODEL ?: "phone" }

    // --- calendar --------------------------------------------------------

    /** The next week of this phone's calendars, as one message for the server. */
    fun calendar(context: Context, days: Int = 7): JSONObject? {
        if (context.checkSelfPermission(Manifest.permission.READ_CALENDAR) != PackageManager.PERMISSION_GRANTED) {
            return null
        }
        val prefs = BuddyPrefs(context)
        val workCalendars = workCalendarIds(context)
        val now = System.currentTimeMillis()
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            ContentUris.appendId(it, now - 60 * 60 * 1000L)
            ContentUris.appendId(it, now + days * 24 * 60 * 60 * 1000L)
        }.build()
        val columns = arrayOf(
            CalendarContract.Instances.EVENT_ID,
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.EVENT_LOCATION,
            CalendarContract.Instances.ALL_DAY,
            CalendarContract.Instances.CALENDAR_DISPLAY_NAME,
            CalendarContract.Instances.CALENDAR_ID,
        )
        val events = JSONArray()
        context.contentResolver.query(uri, columns, null, null, "${CalendarContract.Instances.BEGIN} ASC")?.use { c ->
            while (c.moveToNext() && events.length() < 500) {
                val busyOnly = prefs.workBusyOnly && c.getLong(7) in workCalendars
                events.put(
                    JSONObject()
                        .put("id", c.getLong(0).toString())
                        .put("title", if (busyOnly) "" else c.getString(1).orEmpty())
                        .put("start", Instant.ofEpochMilli(c.getLong(2)).toString())
                        .put("end", Instant.ofEpochMilli(c.getLong(3)).toString())
                        .put("location", if (busyOnly) "" else c.getString(4).orEmpty())
                        .put("all_day", c.getInt(5) == 1)
                        .put("calendar", c.getString(6).orEmpty())
                        .put("busy_only", busyOnly)
                )
            }
        }
        return JSONObject()
            .put("type", "phone_calendar")
            .put("device", deviceName(context))
            .put("events", events)
    }

    /** Calendars that belong to a work account (Exchange, Outlook, Microsoft 365). */
    private fun workCalendarIds(context: Context): Set<Long> {
        val ids = mutableSetOf<Long>()
        val columns = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.ACCOUNT_TYPE,
            CalendarContract.Calendars.ACCOUNT_NAME,
        )
        context.contentResolver.query(CalendarContract.Calendars.CONTENT_URI, columns, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val type = c.getString(1).orEmpty().lowercase()
                if (WORK_ACCOUNT_HINTS.any { it in type }) ids += c.getLong(0)
            }
        }
        return ids
    }

    private val WORK_ACCOUNT_HINTS = listOf("exchange", "outlook", "microsoft", "office")
}

/**
 * Reads the notifications on this phone (once you turn on Notification access for
 * Buddy) and passes them to your server. Buddy's own, ongoing (music, navigation)
 * and system notifications are skipped.
 */
class BuddyNotificationListener : NotificationListenerService() {
    private val recent = ArrayDeque<Int>()

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val n = sbn.notification ?: return
        if (sbn.packageName == packageName || sbn.packageName in IGNORED) return
        if (n.flags and Notification.FLAG_ONGOING_EVENT != 0) return
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val category = n.category
        if (category != null && category in SKIPPED_CATEGORIES) return

        val extras = n.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
        if (title.isBlank() && text.isBlank()) return

        // Apps re-post the same notification often; send each version once.
        val fingerprint = "${sbn.key}|$title|$text".hashCode()
        if (fingerprint in recent) return
        recent.addLast(fingerprint)
        if (recent.size > 200) recent.removeFirst()

        PhoneBridge.send(
            JSONObject()
                .put("type", "notification")
                .put("device", PhoneBridge.deviceName(this))
                .put("app", sbn.packageName)
                .put("app_name", appName(sbn.packageName))
                .put("title", title.take(200))
                .put("text", text.take(1500))
                .put("posted_at", Instant.ofEpochMilli(sbn.postTime).toString())
                .put("key", sbn.key)
        )
    }

    private fun appName(pkg: String): String = KNOWN_APPS[pkg] ?: runCatching {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    }.getOrDefault(pkg)

    companion object {
        private val IGNORED = setOf("android", "com.android.systemui", "com.samsung.android.app.routines")
        private val SKIPPED_CATEGORIES = setOf(
            Notification.CATEGORY_TRANSPORT, Notification.CATEGORY_PROGRESS,
            Notification.CATEGORY_SERVICE, Notification.CATEGORY_SYSTEM,
        )
        private val KNOWN_APPS = mapOf(
            "com.microsoft.teams" to "Teams",
            "com.microsoft.office.outlook" to "Outlook",
            "com.whatsapp" to "WhatsApp",
            "com.google.android.gm" to "Gmail",
            "com.samsung.android.messaging" to "Messages",
            "com.google.android.apps.messaging" to "Messages",
        )
    }
}
