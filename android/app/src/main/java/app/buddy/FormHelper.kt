package app.buddy

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Reads and fills forms in the browser when the user asks Buddy to ("help me fill this form").
 * Turned on by the user in Android's Accessibility settings; Android only gives it Chrome's and
 * Samsung Internet's screens (res/xml/form_helper.xml). It does nothing on its own: it reads the
 * screen only when the server asks, and types or taps only the answers the user said yes to. It
 * never touches password fields and never taps Submit, Pay, Send, Sign in and the like.
 */
class FormHelper : AccessibilityService() {
    private val main = Handler(Looper.getMainLooper())
    private var fields = mutableMapOf<String, AccessibilityNodeInfo>()

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) = Unit  // only acts when asked

    override fun onInterrupt() = Unit

    private fun handle(action: String, id: String, fills: JSONArray?) {
        when (action) {
            "read" -> read(id)
            "scroll" -> {
                val scrolled = rootInActiveWindow?.let { scrollable(it) }
                    ?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD) == true
                main.postDelayed({ read(id, scrolled) }, 900)  // let the page settle first
            }
            "fill" -> fill(id, fills ?: JSONArray())
        }
    }

    private fun read(id: String, scrolled: Boolean = true) {
        val root = rootInActiveWindow
        val app = root?.packageName?.toString().orEmpty()
        val reply = JSONObject().put("type", "form_screen").put("id", id)
        if (root == null || app !in BROWSERS) {
            PhoneBridge.send(reply.put("error", "not_browser").put("app", app))
            return
        }
        fields = mutableMapOf()
        val nodes = JSONArray()
        collect(root, nodes)
        PhoneBridge.send(reply.put("nodes", nodes).put("can_scroll", scrollable(root) != null)
            .put("scrolled", scrolled))
    }

    private fun collect(node: AccessibilityNodeInfo, out: JSONArray, depth: Int = 0) {
        if (depth > 60 || out.length() >= MAX_NODES || !node.isVisibleToUser) return
        val own = (node.text ?: node.contentDescription)?.toString()?.trim().orEmpty()
        val item = JSONObject()
        when {
            node.isPassword -> item.put("kind", "secret")  // never read, never filled
            isDropdown(node) -> item.put("kind", "dropdown").put("value", own)
                .put("t", node.labeledBy?.text?.toString() ?: own.ifEmpty { childText(node) })
            node.isEditable -> item.put("kind", "edit").put("value", own)
                .put("hint", node.hintText?.toString().orEmpty())
                .put("t", node.labeledBy?.text?.toString() ?: node.hintText?.toString().orEmpty())
            node.isCheckable -> item.put("kind", if (node.className?.contains("Radio") == true) "radio" else "check")
                .put("checked", node.isChecked).put("t", own.ifEmpty { childText(node) })
            node.isClickable && (own.isNotEmpty() || childText(node).isNotEmpty()) -> {
                val box = Rect().also { node.getBoundsInScreen(it) }
                item.put("kind", "button").put("t", own.ifEmpty { childText(node) }).put("checked", node.isSelected)
                    .put("x", box.centerX()).put("off", !node.isEnabled)  // where it sits (seat maps), greyed out
            }
            own.isNotEmpty() -> item.put("kind", "text").put("t", own.take(300))
        }
        if (item.has("kind")) {
            if (item.getString("kind") != "text") {
                val key = "f${fields.size + 1}"
                fields[key] = node
                item.put("i", key)
            }
            out.put(item)
            if (item.getString("kind") in listOf("button", "check", "radio", "edit", "secret", "dropdown")) return
        }
        for (i in 0 until node.childCount) node.getChild(i)?.let { collect(it, out, depth + 1) }
    }

    private fun childText(node: AccessibilityNodeInfo, depth: Int = 0): String {
        if (depth > 3) return ""
        return (0 until node.childCount).mapNotNull { node.getChild(it) }.joinToString(" ") { c ->
            (c.text ?: c.contentDescription)?.toString()?.trim()?.ifEmpty { null } ?: childText(c, depth + 1)
        }.trim().take(120)
    }

    private fun scrollable(node: AccessibilityNodeInfo, depth: Int = 0): AccessibilityNodeInfo? {
        if (node.isScrollable) return node
        if (depth > 30) return null
        for (i in 0 until node.childCount) node.getChild(i)?.let { c -> scrollable(c, depth + 1)?.let { return it } }
        return null
    }

    private fun fill(id: String, fills: JSONArray) {
        val done = JSONArray()
        val skipped = JSONArray()
        fun step(i: Int) {
            if (i >= fills.length()) {
                PhoneBridge.send(JSONObject().put("type", "form_result").put("id", id).put("done", done)
                    .put("skipped", skipped))
                return
            }
            val f = fills.getJSONObject(i)
            val key = f.optString("field")
            val node = fields[key]?.also { it.refresh() }
            fun finish(why: String?) {
                if (why == null) done.put(key) else skipped.put(JSONObject().put("field", key).put("why", why))
                main.postDelayed({ step(i + 1) }, STEP_MS)
            }
            when {
                node == null -> finish("not on screen any more")
                node.isPassword -> finish("password field")
                f.has("choose") -> {
                    val wanted = f.getString("choose")
                    if (NEVER_TAP.containsMatchIn(wanted)) finish("submit-type option: you tap it")
                    else if (!tap(node)) finish("couldn't open the dropdown")
                    else main.postDelayed({  // let the list open, then tap the option
                        val option = findOption(wanted, node)
                        finish(if (option != null && tap(option)) null else "option '$wanted' not found in the list")
                    }, OPEN_MS)
                }
                f.has("text") -> finish(if (setText(node, f.getString("text"))) null else "the page didn't accept the text")
                NEVER_TAP.containsMatchIn(label(node)) -> finish("submit-type button: you tap it")
                f.optBoolean("tap") -> finish(if (tap(node)) null else "couldn't tap it")
                else -> finish("nothing to do")
            }
        }
        step(0)
    }

    private fun label(node: AccessibilityNodeInfo): String =
        (node.text ?: node.contentDescription)?.toString()?.ifEmpty { null } ?: childText(node)

    private fun isDropdown(node: AccessibilityNodeInfo): Boolean {
        val role = node.extras?.getCharSequence("AccessibilityNodeInfo.roleDescription")?.toString().orEmpty().lowercase()
        val cls = node.className?.toString().orEmpty()
        return cls.contains("Spinner") || "combo" in role || "pop up" in role || "popup" in role || "list box" in role ||
            "dropdown" in role || label(node).trim().equals("Select an option", ignoreCase = true) ||
            label(node).trim().equals("Select options", ignoreCase = true)
    }

    /** Type into a text box: set it directly, else focus and set, else paste. */
    private fun setText(node: AccessibilityNodeInfo, text: String): Boolean {
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return true
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return true
        getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("Buddy", text))
        return node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }

    /** Tap: the node's own click, else a clickable parent, else a real tap on its middle. */
    private fun tap(node: AccessibilityNodeInfo): Boolean {
        var target: AccessibilityNodeInfo? = node
        repeat(3) {  // web buttons are often a clickable wrapper around the text
            val t = target ?: return@repeat
            if (t.isClickable && t.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true
            target = t.parent
        }
        val r = Rect()
        node.getBoundsInScreen(r)
        if (r.isEmpty) return false
        val path = Path().apply { moveTo(r.exactCenterX(), r.exactCenterY()) }
        return dispatchGesture(GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 60)).build(), null, null)
    }

    /** The open dropdown's option with this text, in any of the browser's windows (lists open in a popup). */
    private fun findOption(wanted: String, dropdown: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val roots = windows.mapNotNull { it.root }.filter { it.packageName?.toString() in BROWSERS }
            .ifEmpty { listOfNotNull(rootInActiveWindow) }
        var partial: AccessibilityNodeInfo? = null
        fun search(n: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
            if (depth > 60 || !n.isVisibleToUser) return null
            val text = (n.text ?: n.contentDescription)?.toString()?.trim().orEmpty()
            if (text.isNotEmpty() && n != dropdown && !n.isEditable) {
                if (text.equals(wanted.trim(), ignoreCase = true)) return n
                if (partial == null && text.startsWith(wanted.trim(), ignoreCase = true)) partial = n
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let { c -> search(c, depth + 1)?.let { return it } }
            return null
        }
        for (root in roots) search(root, 0)?.let { return it }
        return partial
    }

    companion object {
        private const val MAX_NODES = 700  // a seat map has a few hundred seats
        private const val STEP_MS = 450L  // between answers: the page reacts to each one
        private const val OPEN_MS = 900L  // a dropdown's list takes a moment to open
        val BROWSERS = setOf("com.android.chrome", "com.sec.android.app.sbrowser")
        private val NEVER_TAP = Regex("""\b(submit|send|pay|purchase|buy|place order|checkout|check out|confirm|transfer|""" +
            """delete|remove|sign in|log in|login|sign up|register|accept all|agree and|book now|order)\b""",
            RegexOption.IGNORE_CASE)

        @Volatile
        var instance: FormHelper? = null

        fun enabled(context: Context): Boolean {
            val on = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
                .orEmpty()
            return on.split(':').any { ComponentName.unflattenFromString(it)?.className == FormHelper::class.java.name }
        }

        /** A request from the server; false when the helper isn't switched on. */
        fun request(action: String, id: String, fills: JSONArray?): Boolean {
            val helper = instance ?: return false
            helper.main.post { helper.handle(action, id, fills) }
            return true
        }
    }
}
