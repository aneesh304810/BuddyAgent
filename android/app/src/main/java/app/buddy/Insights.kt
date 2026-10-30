package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

private val Card = Color(0xFF151C33)
private val Dim = Color(0xFF8A93B2)
private val Bright = Color(0xFFE0E6FF)
private val Bar = Color(0xFF36C5F0)      // one series: magnitude only, so one hue
private val BarTrack = Color(0xFF222B47)
private val Bad = Color(0xFFFF8A80)

/** Reads the Insights dashboard (GET /admin/insights) and this month's all-in cost (GET /admin/costs). */
object InsightsApi {
    private val http = OkHttpClient.Builder().callTimeout(30, TimeUnit.SECONDS).build()

    private suspend fun get(link: ServerLink, path: String): JSONObject = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + path)
            .header("Authorization", "Bearer ${link.token}")
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                error(if (response.code == 401) "wrong token" else "server said ${response.code}")
            }
            JSONObject(response.body?.string() ?: "{}")
        }
    }

    suspend fun insights(link: ServerLink, days: Int, purpose: String, model: String): JSONObject {
        val q = StringBuilder("/admin/insights?days=$days&recent=100")
        if (purpose.isNotEmpty()) q.append("&purpose=").append(java.net.URLEncoder.encode(purpose, "UTF-8"))
        if (model.isNotEmpty()) q.append("&model=").append(java.net.URLEncoder.encode(model, "UTF-8"))
        return get(link, q.toString())
    }

    suspend fun costs(link: ServerLink): JSONObject = get(link, "/admin/costs")
}

private fun usd(v: Double): String = when {
    v == 0.0 -> "$0"
    v < 0.01 -> String.format(Locale.US, "$%.4f", v)
    else -> String.format(Locale.US, "$%.2f", v)
}

private fun secs(o: JSONObject, key: String): String =
    if (o.isNull(key) || !o.has(key)) "–" else String.format(Locale.US, "%.1fs", o.getDouble(key))

private fun tokens(n: Long): String = when {
    n >= 1_000_000 -> String.format(Locale.US, "%.1fM", n / 1_000_000.0)
    n >= 1_000 -> String.format(Locale.US, "%.1fK", n / 1_000.0)
    else -> n.toString()
}

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

@Composable
fun InsightsScreen(link: ServerLink?) {
    var days by remember { mutableStateOf(7) }
    var purpose by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("") }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var monthAllIn by remember { mutableStateOf<String?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(link, days, purpose, model) {
        if (link == null) {
            problem = "Set up your server first."
            return@LaunchedEffect
        }
        runCatching { InsightsApi.insights(link, days, purpose, model) }
            .onSuccess { data = it; problem = null }
            .onFailure { problem = "Couldn't load insights (${it.message})." }
    }
    LaunchedEffect(link) {
        if (link == null) return@LaunchedEffect
        runCatching { InsightsApi.costs(link) }.onSuccess {
            monthAllIn = "${usd(it.optDouble("month_to_date_usd"))} so far this month, all in " +
                "(heading for ${usd(it.optDouble("forecast_usd"))})"
        }
    }

    val d = data
    LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(1 to "Today", 7 to "7 days", 30 to "30 days", 90 to "90 days").forEach { (n, label) ->
                    Chip(label, days == n) { days = n }
                }
            }
        }
        problem?.let { item { Text(it, color = Bad) } }
        if (d == null) {
            if (problem == null) item { Text("Loading…", color = Dim) }
            return@LazyColumn
        }
        val t = d.getJSONObject("totals")
        monthAllIn?.let { item { Text(it, color = Dim, fontSize = 13.sp) } }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Tile("Model spend", usd(t.optDouble("cost_usd")), Modifier.weight(1f))
                    Tile("Requests", "${t.optInt("requests")}" +
                        if (t.optInt("failed") > 0) " (${t.optInt("failed")} failed)" else "", Modifier.weight(1f))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Tile("Typical reply", secs(t, "chat_median_seconds"), Modifier.weight(1f),
                        "slowest 5%: ${secs(t, "p95_seconds")}")
                    Tile("Free-model calls", "${t.optInt("free_requests")}", Modifier.weight(1f),
                        "of ${t.optInt("requests")}")
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    val hit = if (t.isNull("cache_hit")) "–" else "${(t.optDouble("cache_hit") * 100).toInt()}%"
                    Tile("Cache reuse", hit, Modifier.weight(1f), "of prompt tokens")
                    Tile("Saved by routing", usd(t.optDouble("saved_by_router_usd") + t.optDouble("saved_by_free_usd")),
                        Modifier.weight(1f), "quick model + free models")
                }
            }
        }

        item { Heading("Spend per day") }
        item { DailyBars(d.getJSONArray("by_day").objects()) }

        item { Heading("What it was for") }
        val purposes = d.getJSONArray("by_purpose").objects()
        val maxPurpose = purposes.maxOfOrNull { it.optDouble("cost_usd") }?.takeIf { it > 0 } ?: 1.0
        items(purposes) { p ->
            val used = p.optJSONObject("models") ?: JSONObject()
            val modelText = used.keys().asSequence().joinToString(", ") { "$it ×${used.optInt(it)}" }
            BreakdownRow(
                title = p.optString("label"),
                value = if (p.optDouble("cost_usd") == 0.0 && p.optString("name").startsWith("general:")) "free"
                    else usd(p.optDouble("cost_usd")),
                share = (p.optDouble("cost_usd") / maxPurpose).toFloat(),
                detail = "${p.optInt("requests")} requests · ${secs(p, "median_seconds")} typical · $modelText",
                selected = purpose == p.optString("name"),
            ) { purpose = if (purpose == p.optString("name")) "" else p.optString("name"); model = "" }
        }

        item { Heading("Each model") }
        val models = d.getJSONArray("by_model").objects()
        val maxModel = models.maxOfOrNull { it.optInt("requests").toDouble() }?.takeIf { it > 0 } ?: 1.0
        items(models) { m ->
            val failed = m.optInt("failed")
            BreakdownRow(
                title = "${m.optString("model")} (${m.optString("provider")})",
                value = if (m.optString("provider") != "claude") "free" else usd(m.optDouble("cost_usd")),
                share = (m.optInt("requests") / maxModel).toFloat(),
                detail = "${m.optInt("requests")} requests" + (if (failed > 0) " · $failed failed" else "") +
                    " · ${secs(m, "median_seconds")} typical, ${secs(m, "p95_seconds")} slowest" +
                    " · ${tokens(m.optLong("input_tokens"))} in / ${tokens(m.optLong("output_tokens"))} out" +
                    (if (m.optInt("web_searches") > 0) " · ${m.optInt("web_searches")} searches" else ""),
                selected = model == m.optString("model"),
            ) { model = if (model == m.optString("model")) "" else m.optString("model"); purpose = "" }
        }

        item {
            val filter = listOf(purpose, model).filter { it.isNotEmpty() }.joinToString(" · ")
            Heading(if (filter.isEmpty()) "Latest requests" else "Latest requests: $filter (tap again to clear)")
        }
        val recent = d.getJSONArray("recent").objects()
        if (recent.isEmpty()) item { Text("Nothing yet in this period.", color = Dim) }
        items(recent) { CallRow(it) }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
private fun Chip(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label, color = if (on) Color.Black else Bright, fontSize = 13.sp,
        modifier = Modifier
            .background(if (on) Bar else Card, RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

@Composable
private fun Heading(text: String) {
    Text(text, color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun Tile(label: String, value: String, modifier: Modifier, note: String = "") {
    Column(modifier.background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Text(label, color = Dim, fontSize = 12.sp)
        Text(value, color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
        if (note.isNotEmpty()) Text(note, color = Dim, fontSize = 11.sp)
    }
}

/** One bar per day, one hue; tap a bar to read its numbers. */
@Composable
private fun DailyBars(days: List<JSONObject>) {
    var picked by remember(days) { mutableStateOf(days.lastIndex) }
    val max = days.maxOfOrNull { it.optDouble("cost_usd") }?.takeIf { it > 0 } ?: 1.0
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        days.getOrNull(picked)?.let {
            Text("${it.optString("day")}: ${usd(it.optDouble("cost_usd"))} · ${it.optInt("requests")} requests" +
                " (${it.optInt("free_requests")} free)", color = Bright, fontSize = 13.sp)
        }
        Spacer(Modifier.height(8.dp))
        Row(Modifier.fillMaxWidth().height(110.dp), verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(if (days.size > 31) 1.dp else 2.dp)) {
            days.forEachIndexed { i, day ->
                val share = (day.optDouble("cost_usd") / max).toFloat().coerceIn(0f, 1f)
                Box(
                    Modifier.weight(1f).fillMaxHeight().clickable { picked = i },
                    contentAlignment = Alignment.BottomCenter,
                ) {
                    Box(
                        Modifier.fillMaxWidth()
                            .fillMaxHeight(maxOf(share, 0.02f))
                            .background(
                                if (i == picked) Color.White else Bar,
                                RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp),
                            )
                    )
                }
            }
        }
        Row(Modifier.fillMaxWidth()) {
            Text(days.firstOrNull()?.optString("day")?.drop(5) ?: "", color = Dim, fontSize = 11.sp,
                modifier = Modifier.weight(1f))
            Text(days.lastOrNull()?.optString("day")?.drop(5) ?: "", color = Dim, fontSize = 11.sp)
        }
    }
}

@Composable
private fun BreakdownRow(title: String, value: String, share: Float, detail: String, selected: Boolean,
                         onClick: () -> Unit) {
    Column(
        Modifier.fillMaxWidth()
            .background(if (selected) BarTrack else Card, RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(title, color = Color.White, fontSize = 15.sp, modifier = Modifier.weight(1f))
            Text(value, color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }
        Box(Modifier.fillMaxWidth().padding(vertical = 6.dp).height(6.dp).background(BarTrack, RoundedCornerShape(3.dp))) {
            Box(Modifier.fillMaxWidth(share.coerceIn(0.01f, 1f)).height(6.dp).background(Bar, RoundedCornerShape(3.dp)))
        }
        Text(detail, color = Dim, fontSize = 12.sp)
    }
}

/** One request: when, what for, which model, cost and time; tap for what was asked and answered. */
@Composable
private fun CallRow(c: JSONObject) {
    var open by remember(c.optString("at")) { mutableStateOf(false) }
    val ok = c.optBoolean("ok", true)
    val at = c.optString("at").take(19).replace('T', ' ').drop(5)
    Column(
        Modifier.fillMaxWidth()
            .background(Card, RoundedCornerShape(12.dp))
            .clickable { open = !open }
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).background(if (ok) Bar else Bad, RoundedCornerShape(4.dp)))
            Spacer(Modifier.size(8.dp))
            Text(c.optString("purpose_label"), color = Color.White, fontSize = 14.sp, modifier = Modifier.weight(1f))
            Text("${if (c.optString("provider") == "claude") usd(c.optDouble("cost_usd")) else "free"} · ${secs(c, "seconds")}", color = Bright, fontSize = 13.sp)
        }
        Text("$at UTC · ${c.optString("model")}" + if (!ok) " · ${c.optString("stop")}" else "",
            color = if (ok) Dim else Bad, fontSize = 12.sp)
        if (open) {
            val detail = buildString {
                append("Tokens: ${tokens(c.optLong("input_tokens"))} in, ${tokens(c.optLong("output_tokens"))} out")
                if (c.optLong("cache_read_tokens") > 0) append(", ${tokens(c.optLong("cache_read_tokens"))} from cache")
                if (c.optString("tools").isNotEmpty()) append("\nUsed: ${c.optString("tools")}")
                if (ok && c.optString("stop").isNotEmpty()) append("\nStopped: ${c.optString("stop")}")
            }
            Text(detail, color = Dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
            if (c.optString("request").isNotEmpty()) {
                Text("Asked", color = Dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                Text(c.optString("request"), color = Bright, fontSize = 13.sp)
            }
            if (c.optString("reply").isNotEmpty()) {
                Text("Answered", color = Dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                Text(c.optString("reply"), color = Bright, fontSize = 13.sp, fontFamily = FontFamily.Default)
            }
        }
    }
}
