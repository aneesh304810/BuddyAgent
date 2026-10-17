package app.buddy

import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder

/**
 * Where Buddy's brain lives: the link the server setup script printed,
 * like https://buddy.tailnet.ts.net/?token=abc. HTTPS only, so the token
 * never crosses the network in the clear.
 */
data class ServerLink(val baseUrl: String, val token: String) {

    val socketUrl: String
        get() = baseUrl.replaceFirst("https://", "wss://") + "/ws?token=" +
            URLEncoder.encode(token, "UTF-8")

    companion object {
        /** Read a pasted link. `fallbackToken` keeps the saved token when the link has none. */
        fun parse(input: String, fallbackToken: String? = null): ServerLink? {
            val uri = runCatching { URI(input.trim()) }.getOrNull() ?: return null
            if (uri.scheme?.lowercase() != "https" || uri.host.isNullOrBlank()) return null
            val token = uri.rawQuery
                ?.split("&")
                ?.map { it.split("=", limit = 2) }
                ?.firstOrNull { it.size == 2 && it[0] == "token" }
                ?.let { URLDecoder.decode(it[1], "UTF-8") }
                ?.takeIf { it.isNotBlank() }
                ?: fallbackToken
            if (token.isNullOrBlank()) return null
            val port = if (uri.port == -1) "" else ":${uri.port}"
            return ServerLink("https://${uri.host.lowercase()}$port", token)
        }
    }
}
