package app.buddy

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
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
            node.isEditable -> item.put("kind", "edit").put("value", own)
                .put("hint", node.hintText?.toString().orEmpty())
                .put("t", node.labeledBy?.text?.toString() ?: node.hintText?.toString().orEmpty())
            node.isCheckable -> item.put("kind", if (node.className?.contains("Radio") == true) "radio" else "check")
                .put("checked", node.isChecked).put("t", own.ifEmpty { childText(node) })
            node.isClickable && (own.isNotEmpty() || childText(node).isNotEmpty()) ->
                item.put("kind", "button").put("t", own.ifEmpty { childText(node) }).put("checked", node.isSelected)
            own.isNotEmpty() -> item.put("kind", "text").put("t", own.take(300))
        }
        if (item.has("kind")) {
            if (item.getString("kind") != "text") {
                val key = "f${fields.size + 1}"
                fields[key] = node
                item.put("i", key)
            }
            out.put(item)
            if (item.getString("kind") in listOf("button", "check", "radio", "edit", "secret")) return  // label read
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
        for (i in 0 until fills.length()) {
            val f = fills.getJSONObject(i)
            val key = f.optString("field")
            val node = fields[key]?.also { it.refresh() }
            val why = when {
                node == null -> "not on screen any more"
                node.isPassword -> "password field"
                f.has("text") && !node.isEditable -> "not a text box"
                f.has("text") -> if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply {
                        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, f.getString("text"))
                    })) null else "the page didn't accept the text"
                NEVER_TAP.containsMatchIn((node.text ?: node.contentDescription ?: childText(node)).toString()) ->
                    "submit-type button: you tap it"
                f.optBoolean("tap") -> if (tap(node)) null else "couldn't tap it"
                else -> "nothing to do"
            }
            if (why == null) done.put(key) else skipped.put(JSONObject().put("field", key).put("why", why))
        }
        PhoneBridge.send(JSONObject().put("type", "form_result").put("id", id).put("done", done).put("skipped", skipped))
    }

    private fun tap(node: AccessibilityNodeInfo): Boolean {
        var target: AccessibilityNodeInfo? = node
        repeat(3) {  // web buttons are often a clickable wrapper around the text
            val t = target ?: return false
            if (t.isClickable && t.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true
            target = t.parent
        }
        return false
    }

    companion object {
        private const val MAX_NODES = 350
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
