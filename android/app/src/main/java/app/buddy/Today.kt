package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** One tile of the Today strip: a small label, the thing itself, and where it came from. */
data class TodayItem(val label: String, val title: String, val source: String)

/** What the server's /api/today says: the next thing, the next payment, and how many alerts are new. */
data class TodayState(val next: TodayItem? = null, val due: TodayItem? = null, val newAlerts: Int = 0)

object TodayApi {
    private val http = OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build()

    private fun item(o: JSONObject?, label: String): TodayItem? {
        if (o == null) return null
        val title = o.optString("title")
        if (title.isBlank()) return null
        val whenText = o.optString("when")
        return TodayItem(label, if (whenText.isBlank()) title else "$title, $whenText", o.optString("source"))
    }

    /** Read the strip. Any failure gives an empty strip: the conversation never waits on this. */
    suspend fun load(link: ServerLink): TodayState = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder().url(link.baseUrl + "/api/today")
                .header("Authorization", "Bearer ${link.token}").build()
            http.newCall(request).execute().use { r ->
                if (!r.isSuccessful) return@use TodayState()
                val o = JSONObject(r.body?.string().orEmpty())
                TodayState(
                    next = item(o.optJSONObject("next"), "Next"),
                    due = item(o.optJSONObject("due"), "Due this week"),
                    newAlerts = o.optJSONObject("alerts")?.optInt("new") ?: 0,
                )
            }
        } catch (e: Exception) {
            TodayState()
        }
    }

    /** The Alerts screen was opened: the bell starts counting again from now. */
    suspend fun seen(link: ServerLink) {
        withContext(Dispatchers.IO) {
            try {
                val request = Request.Builder().url(link.baseUrl + "/api/today/seen")
                    .header("Authorization", "Bearer ${link.token}").post(ByteArray(0).toRequestBody()).build()
                http.newCall(request).execute().close()
            } catch (e: Exception) {
                // the count is a convenience; it corrects itself on the next read
            }
        }
    }
}

/** The strip at the top of the conversation: at most two tiles, and nothing at all when there is nothing. */
@Composable
fun TodayStrip(today: TodayState, onOpenMoney: () -> Unit) {
    val next = today.next
    val due = today.due
    if (next == null && due == null) return
    Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (next != null) TodayTile(next, Modifier.weight(1f), null)
        if (due != null) TodayTile(due, Modifier.weight(1f), onOpenMoney)
    }
}

@Composable
private fun TodayTile(item: TodayItem, modifier: Modifier, onClick: (() -> Unit)?) {
    val shape = RoundedCornerShape(14.dp)
    val base = modifier.heightIn(min = 48.dp).clip(shape).background(Pal.panel)
    val tappable = if (onClick != null) base.clickable(role = Role.Button, onClick = onClick) else base
    Column(tappable.padding(horizontal = 12.dp, vertical = 8.dp)) {
        Text(item.label, color = Pal.dim, fontSize = 12.sp)
        Text(item.title, color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 2,
            overflow = TextOverflow.Ellipsis)
        if (item.source.isNotBlank()) Text(item.source, color = Pal.dim, fontSize = 11.sp, maxLines = 1)
    }
}

/** The bell with the number of new alerts on it (no number when there are none). */
@Composable
fun BellButton(count: Int, onClick: () -> Unit) {
    val label = if (count > 0) "Alerts, $count new" else "Alerts"
    Box(Modifier.semantics(mergeDescendants = true) { contentDescription = label }) {
        RoundIconButton(Glyph.BELL, label, onClick)
        if (count > 0) {
            Box(
                Modifier.align(Alignment.TopEnd).size(18.dp).background(Pal.warn, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Text(if (count > 9) "9+" else count.toString(), color = Pal.bg, fontSize = 10.sp,
                    fontWeight = FontWeight.Bold)
            }
        }
    }
}
