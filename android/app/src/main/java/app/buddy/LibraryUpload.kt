package app.buddy

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/**
 * Sends a document, photo, PDF, spreadsheet, CSV or JSON to Buddy's library (POST /api/library).
 * The server answers at once and says in the chat what it found once it has read the file.
 */
object LibraryUpload {
    private const val MAX_BYTES = 25_000_000
    private val http = OkHttpClient.Builder().callTimeout(120, TimeUnit.SECONDS).build()

    /** Upload each file in the background; progress and problems show as chat lines. */
    fun upload(context: Context, uris: List<Uri>, note: String = "") {
        val link = BuddyPrefs(context).link ?: run {
            BuddyState.add(Line("Connect to your server first (Settings).", Speaker.ERROR))
            return
        }
        val resolver = context.applicationContext.contentResolver
        Thread {
            for (uri in uris.take(10)) {
                val name = displayName(context, uri)
                BuddyState.add(Line("📎 $name", Speaker.YOU))
                val bytes = runCatching { resolver.openInputStream(uri)?.use { it.readBytes() } }.getOrNull()
                val problem = when {
                    bytes == null -> "Couldn't open $name."
                    bytes.size > MAX_BYTES -> "$name is over 25 MB."
                    else -> send(link, name, note, bytes)
                }
                if (problem != null) BuddyState.add(Line(problem, Speaker.ERROR))
                else BuddyState.status("Buddy is reading $name…")
            }
        }.start()
    }

    /** null when it worked, else what went wrong. */
    private fun send(link: ServerLink, name: String, note: String, bytes: ByteArray): String? {
        val url = "${link.baseUrl}/api/library?name=${URLEncoder.encode(name, "UTF-8")}" +
            (if (note.isNotBlank()) "&note=${URLEncoder.encode(note.take(400), "UTF-8")}" else "")
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer ${link.token}")
            .post(bytes.toRequestBody("application/octet-stream".toMediaType()))
            .build()
        return runCatching {
            http.newCall(request).execute().use { r ->
                if (r.isSuccessful) null
                else runCatching { JSONObject(r.body?.string().orEmpty()).optString("detail") }.getOrNull()
                    ?.ifBlank { null } ?: "Upload failed (HTTP ${r.code})."
            }
        }.getOrElse { "Upload failed: ${it.message ?: "no connection"}." }
    }

    private fun displayName(context: Context, uri: Uri): String =
        runCatching {
            context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        }.getOrNull() ?: uri.lastPathSegment?.substringAfterLast('/') ?: "upload"
}
