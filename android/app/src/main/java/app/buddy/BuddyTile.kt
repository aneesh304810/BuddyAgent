package app.buddy

import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * Quick Settings tile: swipe down, tap Buddy. Off: opens Buddy and starts listening in Talk (Android only
 * lets the microphone start while Buddy is on screen). On: switches Talk <-> Listen-in. Long-press opens the app.
 */
class BuddyTile : TileService() {
    override fun onStartListening() = refresh()

    override fun onClick() {
        val service = BuddyService.instance
        if (service == null || !BuddyState.ui.value.listening) {
            val open = Intent(this, MainActivity::class.java)
                .setAction(MainActivity.ACTION_LISTEN)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                startActivityAndCollapse(PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE))
            } else {
                @Suppress("DEPRECATION")
                startActivityAndCollapse(open)
            }
            return
        }
        service.startListening(if (BuddyState.ui.value.mode == Mode.TALK) Mode.LISTEN_IN else Mode.TALK)
        refresh()
    }

    private fun refresh() {
        val tile = qsTile ?: return
        val ui = BuddyState.ui.value
        val on = BuddyService.instance != null && ui.listening
        tile.state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "Buddy"
        tile.subtitle = when {
            !on -> "Off"
            ui.mode == Mode.TALK -> "Talk"
            else -> "Listen-in"
        }
        tile.updateTile()
    }

    companion object {
        /** Ask Android to redraw the tile (after Buddy starts, stops or changes mode). */
        fun update(context: Context) = runCatching {
            requestListeningState(context, ComponentName(context, BuddyTile::class.java))
        }
    }
}
