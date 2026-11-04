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
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun MoneyDashboardScreen(link: ServerLink?) {
    if (link == null) {
        Text("Add your Buddy server link in Settings first.", color = Pal.text, modifier = Modifier.padding(16.dp))
        return
    }
    val base = Uri.parse(link.baseUrl)
    val light = Theme.light
    val url = link.baseUrl.trimEnd('/') + "/money.html?token=" + URLEncoder.encode(link.token, "UTF-8") +
        "&theme=" + (if (light) "light" else "default")
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { context ->
            WebView(context).apply {
                setBackgroundColor(AndroidColor.parseColor(Pal.pageBg))
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true      // the page remembers the token and the period
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.setSupportMultipleWindows(false)
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        val u = request.url
                        return !(u.scheme == base.scheme && u.host == base.host && u.port == base.port)
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
