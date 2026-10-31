package app.buddy

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/** A ride app link from Buddy: the app's own link, the web one if the app isn't installed. */
data class RideLink(val app: String, val url: String, val label: String)

/**
 * Opens Uber or Lyft with a trip Buddy filled in. Android only lets an app open another one while
 * it's on screen, so with Buddy in front the ride app opens straight away; otherwise it waits as a
 * notification to tap. "Compare" sends two: the second opens when Buddy is back in front.
 */
object Links {
    private const val NEXT_FOR_MS = 5 * 60_000L
    private const val NOTE_ID = 3
    private val main = Handler(Looper.getMainLooper())
    private var next: RideLink? = null
    private var nextUntil = 0L

    @Volatile
    var buddyInFront = false

    fun open(context: Context, links: List<RideLink>) {
        val first = links.firstOrNull() ?: return
        next = links.getOrNull(1)
        nextUntil = SystemClock.elapsedRealtime() + NEXT_FOR_MS
        if (buddyInFront) launch(context, first) else notify(context, first)
    }

    /** Buddy came back on screen: open the queued second app (compare), once. */
    fun resumed(context: Context) {
        val link = next ?: return
        if (SystemClock.elapsedRealtime() > nextUntil) {
            next = null
            return
        }
        main.postDelayed({  // still in front (not just passing through on the way to the first app)
            if (buddyInFront && next === link) {
                next = null
                launch(context, link)
            }
        }, 700)
    }

    fun launch(context: Context, link: RideLink) {
        context.getSystemService(NotificationManager::class.java).cancel(NOTE_ID)
        for (target in listOf(link.app, link.url).filter { it.isNotEmpty() }) {
            try {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(target)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return
            } catch (e: ActivityNotFoundException) {
                continue  // the app isn't installed: try the web link
            }
        }
    }

    private fun notify(context: Context, link: RideLink) {
        if (context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val open = PendingIntent.getActivity(
            context, 3000,
            Intent(context, MainActivity::class.java).putExtra(MainActivity.EXTRA_RIDE_APP, link.app)
                .putExtra(MainActivity.EXTRA_RIDE_URL, link.url).putExtra(MainActivity.EXTRA_RIDE_LABEL, link.label),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        context.getSystemService(NotificationManager::class.java).notify(
            NOTE_ID,
            Notification.Builder(context, Notes.NUDGES)
                .setSmallIcon(R.drawable.ic_stat_buddy)
                .setContentTitle(link.label)
                .setContentText("Tap to open it with the trip filled in")
                .setAutoCancel(true)
                .setContentIntent(open)
                .build(),
        )
    }
}
