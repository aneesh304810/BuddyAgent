package app.buddy

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Reads the ride prices Uber and Lyft show, so Buddy can compare them. Turned on by the user in
 * Android's Accessibility settings; Android only gives it Uber's and Lyft's screens (see
 * res/xml/ride_reader.xml), and it ignores even those unless Buddy asked for a comparison a moment
 * ago. It never taps anything: it opens each app with the trip filled in, waits for the price list,
 * sends the words on screen to the server (which picks out ride types, prices and minutes away),
 * then comes back to Buddy. The phone has to be unlocked.
 */
class RideReader : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private var queue = ArrayDeque<RideLink>()
    private var reading: RideLink? = null
    private var readingPackage = ""
    private var compareId = ""
    private var sawPrice = false
    private val snapshot = Runnable { finishOne() }
    private val giveUp = Runnable { finishOne() }

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onInterrupt() = Unit

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (reading == null || event.packageName?.toString() != readingPackage) return
        // Wait for the list to stop changing: Uber and Lyft fill prices in over a second or two.
        if (!sawPrice && hasPrice(rootInActiveWindow)) sawPrice = true
        if (sawPrice) {
            main.removeCallbacks(snapshot)
            main.postDelayed(snapshot, SETTLE_MS)
        }
    }

    private fun compare(links: List<RideLink>, id: String) {
        queue = ArrayDeque(links)
        compareId = id
        next()
    }

    private fun next() {
        val link = queue.removeFirstOrNull()
        if (link == null) {
            reading = null
            // Back to Buddy, which says the comparison.
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        reading = link
        readingPackage = PACKAGES[link.label.substringBefore(" ")] ?: ""
        sawPrice = false
        Links.launch(this, link)
        main.postDelayed(giveUp, GIVE_UP_MS)
    }

    private fun finishOne() {
        val link = reading ?: return
        main.removeCallbacks(snapshot)
        main.removeCallbacks(giveUp)
        val root = rootInActiveWindow
        val nodes = JSONArray()
        if (root != null && root.packageName?.toString() == readingPackage) collect(root, nodes)
        PhoneBridge.send(JSONObject().put("type", "ride_screen").put("compare", compareId)
            .put("app", link.label.substringBefore(" ")).put("to", link.label.substringAfter(" to ", ""))
            .put("nodes", nodes).put("last", queue.isEmpty()))
        reading = null
        main.postDelayed({ next() }, 400)
    }

    /** Every piece of text on screen with where it is, top to bottom (no input fields). */
    private fun collect(node: AccessibilityNodeInfo, out: JSONArray, depth: Int = 0) {
        if (depth > 40 || out.length() >= MAX_NODES) return
        val text = (node.text ?: node.contentDescription)?.toString()?.trim().orEmpty()
        if (text.isNotEmpty() && !node.isEditable && !node.isPassword && text.length <= 120) {
            val r = Rect()
            node.getBoundsInScreen(r)
            out.put(JSONObject().put("t", text).put("x", r.left).put("y", r.top).put("r", r.right).put("b", r.bottom))
        }
        for (i in 0 until node.childCount) node.getChild(i)?.let { collect(it, out, depth + 1) }
    }

    private fun hasPrice(node: AccessibilityNodeInfo?, depth: Int = 0): Boolean {
        if (node == null || depth > 40) return false
        val text = (node.text ?: node.contentDescription)?.toString().orEmpty()
        if (PRICE.containsMatchIn(text)) return true
        return (0 until node.childCount).any { i -> hasPrice(node.getChild(i), depth + 1) }
    }

    companion object {
        private const val SETTLE_MS = 2_500L
        private const val GIVE_UP_MS = 20_000L
        private const val MAX_NODES = 400
        private val PRICE = Regex("""(US\$|R\$|S/|[$€£₹])\s?\d""")
        private val PACKAGES = mapOf("Uber" to "com.ubercab", "Lyft" to "me.lyft.android")

        @Volatile
        var instance: RideReader? = null

        fun enabled(context: Context): Boolean {
            val on = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
                .orEmpty()
            return on.split(':').any { ComponentName.unflattenFromString(it)?.className == RideReader::class.java.name }
        }

        /** Read each app's prices in turn; false when the reader isn't switched on. */
        fun compare(links: List<RideLink>, id: String): Boolean {
            val reader = instance ?: return false
            reader.main.post { reader.compare(links, id) }
            return true
        }
    }
}
