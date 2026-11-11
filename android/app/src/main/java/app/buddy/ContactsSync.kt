package app.buddy

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Who's calling: the phone's contacts, names and phone numbers only (no emails, addresses, notes or photos), read
 * with the owner's permission and sent only to their own Buddy server (POST /api/contacts/sync), so Buddy can greet
 * a caller by name when it answers a call the owner missed, and "reply to Priya" finds her chat. Each sync replaces
 * the list on the server. Opt-in: Settings > Share contacts. Turning it off stops syncing and deletes the list on
 * the server (DELETE /api/contacts).
 */
object ContactsSync {
    private val http = OkHttpClient.Builder().callTimeout(60, TimeUnit.SECONDS).build()
    private const val PREFS = "buddy_contacts"
    private const val EVERY_MS = 6 * 60 * 60 * 1000L  // at most every 6 hours when the app opens

    fun allowed(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    /** The owner's "Share contacts" switch. Off until turned on (a phone that already synced before stays on). */
    fun enabled(context: Context): Boolean {
        val p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return if (p.contains("enabled")) p.getBoolean("enabled", false) else p.getLong("synced", 0) > 0
    }

    fun setEnabled(context: Context, on: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("enabled", on).apply()
    }

    /** "Share contacts" off: stop syncing and delete the contacts on the owner's server. Returns how many went. */
    suspend fun forget(context: Context, link: ServerLink?): Int = withContext(Dispatchers.IO) {
        setEnabled(context, false)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong("synced", 0).apply()
        if (link == null) return@withContext 0
        val request = Request.Builder()
            .url(link.baseUrl + "/api/contacts")
            .header("Authorization", "Bearer ${link.token}")
            .delete()
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("server said ${response.code}")
            JSONObject(response.body?.string() ?: "{}").optInt("deleted", 0)
        }
    }

    /** Every contact with a phone number: {"contacts": [{"name", "numbers": [...]}]}. */
    fun collect(context: Context): JSONObject {
        val byName = linkedMapOf<String, MutableSet<String>>()
        val cols = arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER)
        context.contentResolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI, cols, null, null, null)
            ?.use { c ->
                while (c.moveToNext()) {
                    val name = c.getString(0)?.trim().orEmpty()
                    val number = c.getString(1)?.trim().orEmpty()
                    if (name.isNotEmpty() && number.isNotEmpty()) byName.getOrPut(name) { mutableSetOf() }.add(number)
                }
            }
        val list = JSONArray()
        byName.forEach { (name, numbers) -> list.put(JSONObject().put("name", name).put("numbers", JSONArray(numbers.toList()))) }
        return JSONObject().put("contacts", list)
    }

    /** Collect and send to the owner's server. Returns how many contacts were sent. */
    suspend fun sync(context: Context, link: ServerLink): Int = withContext(Dispatchers.IO) {
        if (!enabled(context)) error("Share contacts is off (Settings)")
        val body = collect(context)
        val request = Request.Builder()
            .url(link.baseUrl + "/api/contacts/sync")
            .header("Authorization", "Bearer ${link.token}")
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("server said ${response.code}")
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putLong("synced", System.currentTimeMillis()).apply()
        body.getJSONArray("contacts").length()
    }

    /** When the app opens: sync if allowed and the last sync is over 6 hours old (quietly). */
    suspend fun maybeSync(context: Context, link: ServerLink?) {
        if (link == null || !enabled(context) || !allowed(context)) return
        val last = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong("synced", 0)
        if (System.currentTimeMillis() - last < EVERY_MS) return
        runCatching { sync(context, link) }
    }
}
