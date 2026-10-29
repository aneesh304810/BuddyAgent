package app.buddy

import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.TimeZone
import java.util.concurrent.TimeUnit

/** A message from Buddy's brain (see the protocol at the top of backend/buddy/server.py). */
sealed interface ServerEvent {
    data class Say(val text: String) : ServerEvent
    data object Done : ServerEvent
    data class Nudge(val kind: String, val text: String, val jobId: Int? = null) : ServerEvent
    data class Whisper(val kind: String, val text: String) : ServerEvent
    data class Error(val text: String) : ServerEvent
    data class Approval(val question: String) : ServerEvent

    companion object {
        fun parse(raw: String): ServerEvent? = runCatching {
            val msg = JSONObject(raw)
            when (msg.optString("type")) {
                "say" -> Say(msg.getString("text"))
                "done" -> Done
                "nudge" -> Nudge(
                    msg.optString("kind"),
                    msg.getString("text"),
                    if (msg.has("job_id")) msg.getInt("job_id") else null,
                )
                "whisper" -> Whisper(msg.optString("kind"), msg.getString("text"))
                "error" -> Error(msg.getString("text"))
                "approval" -> Approval(msg.getString("question"))
                else -> null
            }
        }.getOrNull()
    }
}

/**
 * The live connection to the server. Reconnects with backoff, and tells the
 * server the phone's time zone on every connect so Buddy follows you across
 * the US, South America and India. All callbacks arrive on the main thread.
 */
class BuddyClient(
    private val link: ServerLink,
    private val onEvent: (ServerEvent) -> Unit,
    private val onStatus: (Status) -> Unit,
) {
    enum class Status { CONNECTING, ONLINE, OFFLINE, BAD_TOKEN }

    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private val main = Handler(Looper.getMainLooper())
    private var socket: WebSocket? = null
    private var retryMs = 1_000L
    private var closed = false

    fun connect() {
        if (closed || socket != null) return
        onStatus(Status.CONNECTING)
        socket = http.newWebSocket(Request.Builder().url(link.socketUrl).build(), Listener())
    }

    /** Send one message; false when offline. */
    fun send(message: JSONObject): Boolean = socket?.send(message.toString()) ?: false

    fun close() {
        closed = true
        main.removeCallbacksAndMessages(null)
        socket?.close(1000, "bye")
        socket = null
    }

    private inner class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(
                JSONObject()
                    .put("type", "hello")
                    .put("timezone", TimeZone.getDefault().id)
                    .toString()
            )
            main.post {
                if (webSocket !== socket) return@post
                retryMs = 1_000L
                onStatus(Status.ONLINE)
            }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val event = ServerEvent.parse(text) ?: return
            main.post { if (webSocket === socket) onEvent(event) }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            main.post { dropped(webSocket, code) }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            main.post { dropped(webSocket, response?.code ?: 0) }
        }
    }

    private fun dropped(webSocket: WebSocket, code: Int) {
        if (webSocket !== socket) return
        socket = null
        if (closed) return
        // The server refuses a wrong token before the handshake completes (HTTP 403) or with 4401.
        if (code == 403 || code == 4401) {
            onStatus(Status.BAD_TOKEN)
            return
        }
        onStatus(Status.OFFLINE)
        main.postDelayed({ connect() }, retryMs)
        retryMs = (retryMs * 2).coerceAtMost(15_000L)
    }
}
