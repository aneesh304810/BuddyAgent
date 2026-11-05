package app.buddy

import android.annotation.SuppressLint
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import java.net.URLEncoder

/**
 * The money dashboard: the page Buddy's own server draws (web/money.html), shown inside the app.
 * Charts and drill-downs (category -> type -> shop -> each transaction; card, month, transfer recipient).
 * Only Buddy's server may load here: any other address is blocked, so the access token stays on Buddy's page.
 */
@Composable
fun MoneyDashboardScreen(link: ServerLink?) = BuddyPage(link, "money.html")

/** One of Buddy's own pages in the app: dashboards (money, card benefits) and every report, all in one look. */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun BuddyPage(link: ServerLink?, page: String, query: String = "") {
    if (link == null) {
        Text("Add your Buddy server link in Settings first.", color = Pal.text, modifier = Modifier.padding(16.dp))
        return
    }
    val base = Uri.parse(link.baseUrl)
    val light = Theme.light
    val url = link.baseUrl.trimEnd('/') + "/" + page + "?token=" + URLEncoder.encode(link.token, "UTF-8") +
        "&theme=" + (if (light) "light" else "default") + (if (query.isNotEmpty()) "&$query" else "")
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { context ->
            WebView(context).apply {
                // Full size, not wrap-content: otherwise the page's viewport height is wrong and the
                // drill-down panel (a share of the screen's height) opens only one row tall.
                layoutParams = android.view.ViewGroup.LayoutParams(
                    android.view.ViewGroup.LayoutParams.MATCH_PARENT, android.view.ViewGroup.LayoutParams.MATCH_PARENT)
                setBackgroundColor(AndroidColor.parseColor(Pal.pageBg))
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true      // the page remembers the token and the look
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.setSupportMultipleWindows(false)
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        val u = request.url
                        val ours = u.scheme == base.scheme && u.host == base.host && u.port == base.port
                        if (ours && !(u.path ?: "").startsWith("/api/files/")) return false  // Buddy's pages: here
                        // Downloads (signed links) and other sites (issuer pages, bookings): the phone's browser.
                        if (u.scheme == "https") {
                            runCatching {
                                context.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, u)
                                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                            }
                        }
                        return true
                    }
                }
                loadUrl(url)
                tag = light
            }
        },
        update = { view ->  // the look changed in Settings: reload in the new one
            if (view.tag != light) { view.tag = light; view.setBackgroundColor(AndroidColor.parseColor(Pal.pageBg)); view.loadUrl(url) }
        },
    )
}
