package app.buddy

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.SystemClock
import org.json.JSONObject

/**
 * Sends the phone's location to your server when you choose to share it
 * (Settings → Share my location), so "find a spa near me" works. Only when
 * Buddy is opened, connects or is spoken to, at most every few minutes,
 * and only while the app may use location ("while using the app").
 */
object LocationShare {
    private const val EVERY_MS = 10 * 60 * 1000L
    private const val FRESH_MS = 15 * 60 * 1000L
    private var lastSent = 0L

    fun allowed(context: Context): Boolean =
        context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            context.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** Share now if it's on, allowed and due. `force` skips the every-few-minutes limit. */
    fun maybeSend(context: Context, force: Boolean = false) {
        if (!BuddyPrefs(context).shareLocation || !allowed(context)) return
        val now = SystemClock.elapsedRealtime()
        if (!force && now - lastSent < EVERY_MS) return
        lastSent = now
        val manager = context.getSystemService(LocationManager::class.java) ?: return
        val recent = newestKnown(manager)
        if (recent != null && now - recent.elapsedRealtimeNanos / 1_000_000 < FRESH_MS) {
            send(recent)
            return
        }
        val provider = listOf(LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
            .firstOrNull { runCatching { manager.isProviderEnabled(it) }.getOrDefault(false) } ?: return
        try {
            manager.getCurrentLocation(provider, null, context.mainExecutor) { location -> location?.let(::send) }
        } catch (_: SecurityException) {
            // permission withdrawn meanwhile: nothing to send
        }
    }

    fun stopSharing() {
        BuddyService.instance?.sendQuietly(JSONObject().put("type", "location_off"))
    }

    private fun newestKnown(manager: LocationManager): Location? = try {
        manager.getProviders(true).mapNotNull { manager.getLastKnownLocation(it) }
            .maxByOrNull { it.elapsedRealtimeNanos }
    } catch (_: SecurityException) {
        null
    }

    private fun send(location: Location) {
        BuddyService.instance?.sendQuietly(
            JSONObject()
                .put("type", "location")
                .put("lat", location.latitude)
                .put("lng", location.longitude)
                .put("accuracy", location.accuracy.toDouble()),
        )
    }
}
