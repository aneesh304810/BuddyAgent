package app.buddy

import android.os.Build
import android.webkit.WebView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * The phone's own update levels for the Security page (securitywatch.py): Android's security patch date, the maker,
 * the Android version, and the web view's version and the day it was last updated. Nothing personal. A flaw that
 * CISA lists as exploited closes once the phone's update is dated on or after the listing; without this report it
 * stays open, since the server can't see the phone.
 */
object PhoneCheck {
    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()
    private var sent = false  // once each time the app starts

    fun levels(): JSONObject {
        val o = JSONObject()
        o.put("security_patch", Build.VERSION.SECURITY_PATCH.orEmpty())  // "2026-11-01"
        o.put("android", Build.VERSION.RELEASE.orEmpty())
        o.put("maker", Build.MANUFACTURER.orEmpty())
        val web = runCatching { WebView.getCurrentWebViewPackage() }.getOrNull()
        if (web != null) {
            o.put("webview", web.versionName.orEmpty())
            if (web.lastUpdateTime > 0) {
                o.put("webview_updated", SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date(web.lastUpdateTime)))
            }
        }
        return o
    }

    /** Tell the server. Any failure is quiet: it is tried again the next time the app starts. */
    suspend fun send(link: ServerLink) {
        if (sent) return
        withContext(Dispatchers.IO) {
            try {
                val body = levels().toString().toRequestBody("application/json".toMediaType())
                val request = Request.Builder().url(link.baseUrl.trimEnd('/') + "/api/security/device")
                    .header("Authorization", "Bearer ${link.token}").post(body).build()
                http.newCall(request).execute().use { if (it.isSuccessful) sent = true }
            } catch (e: Exception) {
                // the Security page keeps the phone's flaws open until a report arrives
            }
        }
    }
}
