package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

private val Card: Color get() = Pal.panel
private val Dim: Color get() = Pal.dim
private val Bright: Color get() = Pal.bright
private val Bar: Color get() = Pal.bar      // one series: magnitude only, so one hue
private val BarTrack: Color get() = Pal.track
private val Bad: Color get() = Pal.bad

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
        // days = 0: since the last cost reset (Budgets, start fresh)
        val q = StringBuilder(if (days == 0) "/admin/insights?days=30&since_reset=true&recent=100"
                              else "/admin/insights?days=$days&recent=100")
        if (purpose.isNotEmpty()) q.append("&purpose=").append(java.net.URLEncoder.encode(purpose, "UTF-8"))
        if (model.isNotEmpty()) q.append("&model=").append(java.net.URLEncoder.encode(model, "UTF-8"))
        return get(link, q.toString())
    }

    suspend fun costs(link: ServerLink): JSONObject = get(link, "/admin/costs")

    suspend fun health(link: ServerLink): JSONObject = get(link, "/admin/health?history=14")

    suspend fun budgets(link: ServerLink): JSONObject = get(link, "/admin/budgets")

    suspend fun setBudget(link: ServerLink, which: String, usd: Double): JSONObject =
        post(link, "/admin/budgets", JSONObject().put("which", which).put("usd", usd))

    suspend fun fresh(link: ServerLink): JSONObject = post(link, "/admin/budgets/fresh", JSONObject())

    suspend fun pace(link: ServerLink, approve: Boolean): JSONObject =
        post(link, "/admin/budgets/pace", JSONObject().put("approve", approve))

    private suspend fun post(link: ServerLink, path: String, json: JSONObject): JSONObject = withContext(Dispatchers.IO) {
        val body = json.toString()
        val request = Request.Builder()
            .url(link.baseUrl + path)
            .header("Authorization", "Bearer ${link.token}")
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { response ->
            val text = response.body?.string() ?: "{}"
            if (!response.isSuccessful) error(runCatching { JSONObject(text).optString("detail") }.getOrNull()
                ?.takeIf { it.isNotEmpty() } ?: "server said ${response.code}")
            JSONObject(text)
        }
    }

    suspend fun runHealth(link: ServerLink): JSONObject = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + "/admin/health/run")
            .header("Authorization", "Bearer ${link.token}")
            .post(ByteArray(0).toRequestBody(null))
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("server said ${response.code}")
            JSONObject(response.body?.string() ?: "{}")
        }
    }
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

private fun pct(o: JSONObject, key: String): String =
    if (o.isNull(key) || !o.has(key)) "–" else "${(o.getDouble(key) * 100).toInt()}%"

/** Accuracy of free answers (Claude's review) and your thumbs, when there are any. */
private fun quality(o: JSONObject): String {
    val parts = mutableListOf<String>()
    if (o.optInt("reviewed") > 0) parts += "right as given ${pct(o, "accuracy")}, usable ${pct(o, "usable")} " +
        "(${o.optInt("reviewed")} checked)"
    if (o.optInt("failed_check") > 0) parts += "${o.optInt("failed_check")} failed the auto-check"
    if (o.optInt("thumbs_up") + o.optInt("thumbs_down") > 0) parts +=
        "\uD83D\uDC4D ${o.optInt("thumbs_up")} \uD83D\uDC4E ${o.optInt("thumbs_down")}"
    return if (parts.isEmpty()) "" else "\n" + parts.joinToString(" · ")
}

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

@Composable
fun InsightsScreen(link: ServerLink?) {
    var days by remember { mutableStateOf(7) }
    var purpose by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("") }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var monthAllIn by remember { mutableStateOf<String?>(null) }
    var costLines by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var agentCosts by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var problem by remember { mutableStateOf<String?>(null) }
    var healthData by remember { mutableStateOf<JSONObject?>(null) }
    var checking by remember { mutableStateOf(false) }
    var budgetData by remember { mutableStateOf<JSONObject?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(link) {
        if (link == null) return@LaunchedEffect
        runCatching { InsightsApi.budgets(link) }.onSuccess { budgetData = it }
    }

    LaunchedEffect(link) {
        if (link == null) return@LaunchedEffect
        runCatching { InsightsApi.health(link) }.onSuccess { healthData = it }
    }

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
                "(heading for ${usd(it.optDouble("forecast_usd"))}, about ${usd(it.optDouble("forecast_yearly_usd"))} a year)" +
                (it.optJSONArray("price_not_set")?.let { arr -> if (arr.length() > 0) ". Price not set yet: " +
                    (0 until arr.length()).joinToString(", ") { i -> arr.optString(i) } else "" } ?: "")
            costLines = it.optJSONArray("lines")?.objects().orEmpty()
            agentCosts = it.optJSONArray("agents")?.objects().orEmpty()
        }
    }

    val d = data
    LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(0 to "Since reset", 1 to "Today", 7 to "7 days", 30 to "30 days", 90 to "90 days").forEach { (n, label) ->
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
        if (days == 0) item {
            Text(if (d.optBoolean("since_reset")) "Since the cost reset on ${d.optString("since").take(16).replace('T', ' ')} UTC " +
                     "(earlier costs are in Reports, \"Cost archive\")"
                 else "No cost reset yet: showing the last 30 days. Start one in Budgets (Start fresh from now).",
                 color = Dim, fontSize = 12.sp)
        }
        d.optJSONArray("failing")?.objects()?.takeIf { it.isNotEmpty() }?.let { bad ->
            item { Heading("What's failing") }
            items(bad) { f ->
                Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
                    Text("${f.optString("label")} · ${f.optString("provider")}: ${f.optInt("failed")} of " +
                         "${f.optInt("requests")} failed (${(f.optDouble("rate", 0.0) * 100).toInt()}%)",
                         color = Pal.text, fontSize = 13.sp)
                    f.optString("reason").takeIf { it.isNotEmpty() }?.let { Text(it, color = Dim, fontSize = 11.sp) }
                }
            }
        }
        monthAllIn?.let { item { Text(it, color = Dim, fontSize = 13.sp) } }
        budgetData?.let { b -> item { BudgetCard(b, link) { budgetData = it } } }
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
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Tile("Free answers right", pct(t, "accuracy"), Modifier.weight(1f),
                        "usable ${pct(t, "usable")} · ${t.optInt("reviewed")} checked by Claude")
                    Tile("Your rating", pct(t, "satisfaction"), Modifier.weight(1f),
                        "\uD83D\uDC4D ${t.optInt("thumbs_up")}  \uD83D\uDC4E ${t.optInt("thumbs_down")}")
                }
            }
        }

        item { Heading("How it's doing") }
        item {
            HealthCard(healthData, checking) {
                if (link != null && !checking) {
                    checking = true
                    scope.launch {
                        runCatching { InsightsApi.runHealth(link); InsightsApi.health(link) }
                            .onSuccess { healthData = it }
                            .onFailure { problem = "Couldn't run the check (${it.message})." }
                        checking = false
                    }
                }
            }
        }

        val sp = d.optJSONObject("speed") ?: JSONObject()
        item { Heading("Speed (from your side)") }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Tile("First words", secs(sp, "first_word_median"), Modifier.weight(1f),
                        "slowest 5%: ${secs(sp, "first_word_p95")}")
                    Tile("Whole reply", secs(sp, "total_median"), Modifier.weight(1f),
                        "slowest 5%: ${secs(sp, "total_p95")}")
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    val outcomes = sp.optJSONObject("outcomes") ?: JSONObject()
                    val bad = outcomes.optInt("error") + outcomes.optInt("too_many_steps") + outcomes.optInt("refusal") +
                        outcomes.optInt("silent")
                    Tile("Replies", "${sp.optInt("replies")}", Modifier.weight(1f),
                        "$bad failed · ${outcomes.optInt("interrupted")} interrupted")
                    Tile("Needed 'one sec'", pct(sp, "filler_rate"), Modifier.weight(1f),
                        "handed over: ${pct(sp, "handed_over_rate")} · ${sp.optDouble("avg_rounds", 0.0)} steps")
                }
            }
        }
        val routes = sp.optJSONArray("by_route")?.objects() ?: emptyList()
        if (routes.isNotEmpty()) item {
            Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
                Text("By model route", color = Dim, fontSize = 12.sp)
                routes.forEach { r ->
                    Text("${r.optString("route")}: ${r.optInt("replies")} replies · first words " +
                        "${secs(r, "first_word_median")} · whole ${secs(r, "total_median")}", color = Bright, fontSize = 13.sp)
                }
                val tools = sp.optJSONArray("slowest_tools")?.objects() ?: emptyList()
                if (tools.isNotEmpty()) {
                    Text("Slowest replies used", color = Dim, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
                    tools.forEach { t ->
                        Text("${t.optString("tool")}: ${secs(t, "total_median")} typical (${t.optInt("replies")} replies)",
                            color = Bright, fontSize = 13.sp)
                    }
                }
            }
        }

        item { Heading("Tokens and requests per model") }
        item {
            Text("Tap a request type to see its calls below.", color = Dim, fontSize = 12.sp)
        }
        items(d.getJSONArray("by_model").objects(), key = { "tok" + it.optString("name") }) { m ->
            ModelTokens(m, picked = purpose) { p -> purpose = if (purpose == p) "" else p; model = "" }
        }

        val experience = d.optJSONObject("experience")
        if (experience != null && experience.optBoolean("on", true)) {
            item { Heading("Experience health") }
            item { ExperienceHealth(experience, link) }
        }

        val voice = d.optJSONObject("voice")
        if (voice != null && voice.optInt("utterances") > 0) {
            item { Heading("Voice: from your last word to Buddy's first") }
            item { VoiceTimings(voice) }
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
                detail = "${p.optInt("requests")} requests · ${secs(p, "median_seconds")} typical · $modelText" + quality(p),
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
                    (if (m.optInt("web_searches") > 0) " · ${m.optInt("web_searches")} searches" else "") +
                    quality(m),
                selected = model == m.optString("model"),
            ) { model = if (model == m.optString("model")) "" else m.optString("model"); purpose = "" }
        }

        val freeInfo = d.optJSONObject("free_models")
        if (freeInfo != null) {
            item { Heading("Free models") }
            if (freeInfo.optString("note").isNotEmpty()) item { Text(freeInfo.optString("note"), color = Dim, fontSize = 13.sp) }
            if (freeInfo.optBoolean("direct_answers")) item {
                Text("Simple general questions (\"what is…\", \"how do you say…\") go straight to these; " +
                    "anything personal stays on Claude.", color = Dim, fontSize = 12.sp)
            }
            items(freeInfo.optJSONArray("providers")?.objects() ?: emptyList()) { p -> FreeModelRow(p) }
        }

        val web = d.optJSONObject("web")
        if (web != null) {
            item { Heading("Web reading (this month)") }
            item {
                val pages = web.optInt("pages_read")
                Text(
                    (if (web.optBoolean("serper")) "Google search (Serper) is on. " else
                        "Google search isn't connected (BUDDY_SERPER_KEY), so searches use Claude's. ") +
                        if (pages == 0) "No pages read by Buddy yet." else
                        "$pages pages read by Buddy · ${web.optInt("digested")} digested by a free model · " +
                        "${web.optInt("raw")} given to Claude trimmed · ${web.optInt("failed")} couldn't open · " +
                        "${tokens(web.optLong("tokens_saved"))} tokens Claude didn't have to read " +
                        "(about ${"$"}${"%.2f".format(web.optDouble("saved_usd"))} saved)",
                    color = Dim, fontSize = 13.sp,
                )
            }
        }

        val speech = d.optJSONObject("speech")
        if (speech != null && (speech.optBoolean("groq_on") || speech.optInt("groq") + speech.optInt("local") > 0)) {
            item { Heading("Hearing you (this month)") }
            item {
                val skipped = speech.optInt("groq_skipped")
                Text(
                    (if (speech.optBoolean("groq_on")) "Talk mode uses Groq's Whisper (fast, better Malayalam); " +
                        "Listen-in stays on your server. " else "Everything is heard by your server's Whisper " +
                        "(add BUDDY_GROQ_KEY for faster Talk mode). ") +
                        "${speech.optInt("groq")} clips by Groq · ${speech.optInt("local")} by your server" +
                        (if (skipped > 0) " · $skipped times Groq was skipped (limit, error or other language)" else ""),
                    color = Dim, fontSize = 13.sp,
                )
            }
            val mics = speech.optJSONObject("listen_mics")
            if (mics != null && mics.length() > 0) item {
                Text(
                    "Listen-in by microphone:\n" + listOf("phone" to "Phone mic", "headset" to "Headset mic (in pocket)")
                        .mapNotNull { (k, label) ->
                            mics.optJSONObject(k)?.let { q ->
                                "$label: ${q.optInt("clips")} clips · confidence " +
                                    (if (q.isNull("confidence")) "–" else "%.2f".format(q.optDouble("confidence"))) +
                                    " · ${q.optInt("dropped_pct")}% dropped · ${q.optInt("unsure_pct")}% unclear"
                            }
                        }.joinToString("\n"),
                    color = Dim, fontSize = 13.sp,
                )
            }
            val ns = speech.optJSONObject("noise_suppressor")
            if (ns != null && ns.length() > 0) item {
                Text(
                    "Noise suppressor (higher confidence, fewer dropped = better):\n" +
                        listOf("off" to "Not cleaned", "on" to "Cleaned").mapNotNull { (k, label) ->
                            ns.optJSONObject(k)?.let { q ->
                                "$label: ${q.optInt("clips")} clips · confidence " +
                                    (if (q.isNull("confidence")) "–" else "%.2f".format(q.optDouble("confidence"))) +
                                    " · ${q.optInt("dropped_pct")}% dropped · ${q.optInt("unsure_pct")}% unclear"
                            }
                        }.joinToString("\n"),
                    color = Dim, fontSize = 13.sp,
                )
            }
        }

        val dataTools = d.optJSONObject("data_tools")
        if (dataTools != null && dataTools.optInt("runs") > 0) {
            item { Heading("Data tools (this month)") }
            item {
                Text("${dataTools.optInt("runs")} runs in the sandbox · ${tokens(dataTools.optLong("tokens_saved"))} " +
                    "tokens the AI didn't have to read (exact numbers by code)", color = Dim, fontSize = 13.sp)
            }
        }

        if (costLines.isNotEmpty()) {
            item { Heading("What each service costs this month") }
            item {
                Text("Every service and agent Buddy is connected to. Live: the provider's own bill. Buddy meter: " +
                    "Buddy counts each call at its price. Your price / plan price: the amount you set, because the " +
                    "provider has no billing API.", color = Dim, fontSize = 12.sp)
            }
            val paid = costLines.filter { it.optString("source") != "free" }
            val top = paid.maxOfOrNull { it.optDouble("forecast") }?.takeIf { it > 0 } ?: 1.0
            items(paid) { c -> ServiceCost(c, (c.optDouble("forecast") / top).toFloat()) }
            val free = costLines.filter { it.optString("source") == "free" }
            if (free.isNotEmpty()) {
                item { Text("Free (${free.size})", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold) }
                items(free) { c ->  // each free service with what it's for and its plan
                    Column(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                        Row {
                            Text(c.optString("provider"), color = Pal.text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                            Text("$0", color = Dim, fontSize = 13.sp)
                        }
                        c.optString("note").takeIf { it.isNotBlank() }?.let { Text(it, color = Dim, fontSize = 12.sp) }
                    }
                }
            }
            if (agentCosts.isNotEmpty()) {
                item { Text("Agents this month", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold) }
                items(agentCosts) { a ->
                    val cost = a.optDouble("external_cost_usd")
                    BreakdownRow(
                        title = a.optString("agent").replaceFirstChar { it.uppercase() },
                        value = if (cost > 0) usd(cost) else "no extra cost",
                        share = 0f,
                        detail = "${a.optInt("calls_this_month")} calls" +
                            a.optString("note").takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty(),
                        selected = false, onClick = {},
                    )
                }
            }
        }

        val tools = d.optJSONArray("tools")?.objects() ?: emptyList()
        item { Heading("Tools and agents") }
        if (tools.isEmpty()) item { Text("No tools used in this period.", color = Dim, fontSize = 13.sp) }
        items(tools) { t -> ToolRow(t) }

        val apis = d.optJSONObject("apis")
        if (apis != null) {
            item { Heading("Travel and data APIs") }
            item {
                val acct = apis.optJSONObject("serpapi_account")
                Text("${usd(apis.optDouble("cost_usd"))} in this period · ${usd(apis.optDouble("month_cost_usd"))} this month" +
                    (if (acct != null && !acct.isNull("plan_searches_left"))
                        " · SerpApi: ${acct.optInt("plan_searches_left")} of ${acct.optInt("searches_per_month")} searches left (${acct.optString("plan_name")})"
                    else ""), color = Dim, fontSize = 12.sp)
            }
            val providers = apis.optJSONArray("providers")?.objects().orEmpty()
            if (providers.isEmpty()) item { Text("No outside API calls in this period.", color = Dim, fontSize = 13.sp) }
            val most = providers.maxOfOrNull { it.optInt("calls") }?.takeIf { it > 0 } ?: 1
            items(providers) { p ->
                val ends = p.optJSONObject("endpoints")
                val endText = ends?.keys()?.asSequence()?.joinToString(", ") { "$it ×${ends.optInt(it)}" }.orEmpty()
                val failed = p.optInt("failed")
                BreakdownRow(
                    title = p.optString("provider").replaceFirstChar { it.uppercase() },
                    value = if (p.optDouble("cost_usd") > 0) usd(p.optDouble("cost_usd")) else "free",
                    share = (p.optInt("calls").toFloat() / most),
                    detail = "${p.optInt("calls")} calls" + (if (failed > 0) " · $failed failed" else "") +
                        (if (endText.isNotBlank()) " · $endText" else "") +
                        (if (p.optString("last_error").isNotBlank()) " · last error: ${p.optString("last_error").take(80)}" else ""),
                    selected = false,
                ) { }
            }
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
private fun FreeModelRow(p: JSONObject) {
    val on = p.optBoolean("on")
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).background(if (on) Bar else Dim, RoundedCornerShape(4.dp)))
            Spacer(Modifier.size(8.dp))
            Text(p.optString("name"), color = Pal.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
            Text(if (on) "on" else if (p.optBoolean("key_set")) "key set, off" else "no key", color = if (on) Bar else Dim,
                fontSize = 12.sp)
        }
        Text(p.optString("model") + " · " + p.optString("good_at"), color = Dim, fontSize = 12.sp)
        val first = p.optJSONArray("first_choice_for")
        if (first != null && first.length() > 0) {
            Text("First choice for: " + (0 until first.length()).joinToString(", ") { first.getString(it) },
                color = Bright, fontSize = 12.sp)
        }
        if (p.optInt("requests") > 0) {
            Text("${p.optInt("requests")} requests" + (if (p.optInt("failed") > 0) " · ${p.optInt("failed")} failed" else "") +
                " · ${secs(p, "median_seconds")} typical" +
                (if (!p.isNull("accuracy")) " · right ${pct(p, "accuracy")}" else ""), color = Bright, fontSize = 12.sp)
        }
    }
}

@Composable
private fun ToolRow(t: JSONObject) {
    val who = when (t.optString("agent")) {
        "buddy" -> "Buddy"
        "claude" -> "Claude (server tool)"
        else -> t.optString("agent").replaceFirstChar { it.uppercase() } + " agent"
    }
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(t.optString("tool").replace('_', ' '), color = Pal.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
            Text(if (t.optDouble("extra_usd") > 0) usd(t.optDouble("extra_usd")) else "no extra cost", color = Bright, fontSize = 12.sp)
        }
        Text("$who · ${t.optInt("calls")} calls" + (if (t.optInt("failed") > 0) " · ${t.optInt("failed")} failed" else "") +
            (if (!t.isNull("median_seconds")) " · ${secs(t, "median_seconds")} typical" else ""), color = Dim, fontSize = 12.sp)
        if (t.optString("last_error").isNotEmpty()) Text("Last error: ${t.optString("last_error")}", color = Bad, fontSize = 12.sp)
    }
}

/** The latest daily health check: status, what to fix, and the trend. */
@Composable
private fun HealthCard(data: JSONObject?, checking: Boolean, onRun: () -> Unit) {
    val latest = data?.optJSONObject("latest")
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val status = latest?.optString("status") ?: ""
            val label = when (status) {
                "good" -> "All good"
                "watch" -> "Worth watching"
                "act" -> "Needs a fix"
                else -> "No check yet"
            }
            Box(Modifier.size(10.dp).background(
                when (status) { "act" -> Bad; "watch" -> Pal.warn; "good" -> Bar; else -> Dim },
                RoundedCornerShape(5.dp)))
            Spacer(Modifier.size(8.dp))
            Text(label, color = Pal.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
            Chip(if (checking) "Checking…" else "Check now", false, onRun)
        }
        if (latest != null) {
            Text("Checked ${latest.optString("at").take(16).replace('T', ' ')} UTC, comparing the last 7 days " +
                "with the week before.", color = Dim, fontSize = 12.sp)
            val findings = latest.optJSONArray("findings")?.objects() ?: emptyList()
            if (findings.isEmpty()) Text("Nothing got worse.", color = Bright, fontSize = 13.sp,
                modifier = Modifier.padding(top = 6.dp))
            findings.forEach { f ->
                var open by remember(f.optString("title")) { mutableStateOf(false) }
                Column(Modifier.fillMaxWidth().padding(top = 8.dp).clickable { open = !open }) {
                    Text((if (f.optString("severity") == "act") "● " else "○ ") + f.optString("title"),
                        color = if (f.optString("severity") == "act") Bad else Pal.warn, fontSize = 14.sp)
                    Text(f.optString("area"), color = Dim, fontSize = 11.sp)
                    if (open) {
                        Text(f.optString("detail"), color = Bright, fontSize = 13.sp)
                        Text("Fix: " + f.optString("fix"), color = Pal.text, fontSize = 13.sp,
                            modifier = Modifier.padding(top = 4.dp))
                    }
                }
            }
        } else {
            Text("The first check runs early tomorrow morning, or tap Check now.", color = Dim, fontSize = 12.sp)
        }
        val history = data?.optJSONArray("history")?.objects() ?: emptyList()
        if (history.size > 1) {
            Text("Past checks (newest first)", color = Dim, fontSize = 12.sp, modifier = Modifier.padding(top = 10.dp))
            history.take(7).forEach { h ->
                Text("${h.optString("at").take(10)} · ${h.optString("status")} · first words ${secs(h, "first_word_median")}" +
                    " · free right ${pct(h, "accuracy")} · you ${pct(h, "satisfaction")}",
                    color = Bright, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
            }
        }
    }
}

/** Diagnostics: microphone, background, network and server, the tips they lead to, and the slowest moments. */
@Composable
private fun ExperienceHealth(h: JSONObject, link: ServerLink?) {
    val warn = Pal.warn
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    var shareProblem by remember { mutableStateOf<String?>(null) }
    fun ms(v: Double) = if (v.isNaN()) "–" else if (v >= 1000) "%.1f s".format(v / 1000) else "%.0f ms".format(v)
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val tips = h.optJSONArray("tips")
        if (tips != null && tips.length() > 0) {
            for (i in 0 until tips.length()) Text("⚠ ${tips.getString(i)}", color = warn, fontSize = 13.sp)
        } else {
            Text("✓ Nothing stands out in this period (${h.optInt("events")} events).", color = Pal.good, fontSize = 13.sp)
        }
        val mic = h.optJSONObject("microphone")
        val byMic = mic?.optJSONObject("by_mic")
        if (byMic != null && byMic.length() > 0) {
            Text("Microphone", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            byMic.keys().forEach { m ->
                val x = byMic.getJSONObject(m)
                Text("$m: voice ${x.optDouble("snr_db").let { if (it.isNaN()) "–" else "%.0f".format(it) }} dB over background · " +
                    "${x.optInt("low_snr_pct")}% noisy · ${x.optInt("clipped_pct")}% distorted · ${x.optInt("clips")} clips",
                    color = Bright, fontSize = 12.sp)
            }
            val dropped = mic.optJSONObject("dropped_clips")
            if (dropped != null && dropped.length() > 0) Text("Thrown away: " + dropped.keys().asSequence()
                .joinToString { "${it.replace('_', ' ')} ${dropped.optInt(it)}" }, color = Dim, fontSize = 12.sp)
            val ev = mic.optJSONObject("events")
            if (ev != null && ev.length() > 0) Text("Mic events: " + ev.keys().asSequence()
                .joinToString { "${it.replace('_', ' ')} ${ev.optInt(it)}" }, color = Dim, fontSize = 12.sp)
        }
        h.optJSONObject("network")?.let { n ->
            Text("Network", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            Text("Ping ${ms(n.optDouble("ping_ms"))} (slow ${ms(n.optDouble("ping_p95_ms"))}) · ${n.optInt("disconnects")} drops " +
                "(${n.optDouble("disconnects_per_day")}/day) · back in ${ms(n.optDouble("reconnect_ms"))}", color = Bright, fontSize = 12.sp)
        }
        h.optJSONObject("background")?.let { b ->
            val ev = b.optJSONObject("events")
            if (ev != null && ev.length() > 0) {
                Text("Background", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Text(ev.keys().asSequence().joinToString { "${it.replace('_', ' ')} ${ev.optInt(it)}" } +
                    (if (!b.isNull("battery_saver_pct")) " · battery saver ${b.optInt("battery_saver_pct")}% of the time" else ""),
                    color = Bright, fontSize = 12.sp)
            }
        }
        h.optJSONObject("speech")?.let { sp ->
            val eng = sp.optJSONObject("engines")
            if (eng != null && eng.length() > 0) {
                Text("Speech to text", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Text(eng.keys().asSequence().joinToString(" · ") { e ->
                    val x = eng.getJSONObject(e); "$e ${ms(x.optDouble("ms"))} (slow ${ms(x.optDouble("p95_ms"))}, ${x.optInt("clips")}×)"
                }, color = Bright, fontSize = 12.sp)
            }
            val err = sp.optJSONObject("errors")
            if (err != null && err.length() > 0) Text(err.keys().asSequence().joinToString { "$it ${err.optInt(it)}" },
                color = Dim, fontSize = 12.sp)
        }
        h.optJSONObject("server")?.let { sv ->
            if (sv.optInt("slow_replies") > 0) {
                Text("Server", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Text("${sv.optInt("slow_replies")} slow replies, ${sv.optInt("slow_while_busy")} while the server was busy",
                    color = Bright, fontSize = 12.sp)
            }
        }
        val slow = h.optJSONArray("slowest")?.objects().orEmpty()
        if (slow.isNotEmpty()) {
            Text("Slowest moments", color = Pal.text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            slow.take(10).forEach { m ->
                val st = m.optJSONObject("steps") ?: JSONObject()
                val parts = listOf("pause" to "pause", "network" to "net", "speech_to_text" to "stt", "to_buddy" to "to Buddy",
                    "first_word" to "answer").filter { !st.isNull(it.first) }
                    .joinToString(" + ") { "${it.second} ${ms(st.optDouble(it.first))}" }
                val extra = listOfNotNull(m.optString("engine").takeIf { it.isNotBlank() },
                    m.optString("mic").takeIf { it.isNotBlank() && it != "null" },
                    if (!m.isNull("snr_db")) "voice ${"%.0f".format(m.optDouble("snr_db"))} dB" else null,
                    if (!m.isNull("server_load")) "server load ${m.optDouble("server_load")}" else null,
                    if (!m.isNull("ping_ms")) "ping ${ms(m.optDouble("ping_ms"))}" else null).joinToString(" · ")
                Text((if (m.optString("missed").isNotBlank()) "✗ missed (${m.optString("missed")})" else "⏱ ${ms(m.optDouble("total_ms"))}") +
                    " · ${m.optString("at").take(16).replace('T', ' ')} UTC", color = if (m.optString("missed").isNotBlank()) warn else Bright,
                    fontSize = 12.sp)
                if (parts.isNotBlank()) Text("   $parts", color = Dim, fontSize = 11.sp)
                if (extra.isNotBlank()) Text("   $extra", color = Dim, fontSize = 11.sp)
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("csv" to "Export CSV", "json" to "Export JSON").forEach { (f, label) ->
                androidx.compose.material3.OutlinedButton(enabled = link != null, onClick = {
                    scope.launch { runCatching { DiagApi.share(context, link!!, f) }.onFailure { shareProblem = it.message } }
                }) { Text(label) }
            }
        }
        shareProblem?.let { Text("Couldn't export: $it", color = warn, fontSize = 12.sp) }
    }
}

/** Where the time goes after you stop talking, step by step (medians), and what was missed. */
@Composable
private fun VoiceTimings(v: JSONObject) {
    fun s(ms: Double) = if (ms.isNaN() || ms < 0) "–" else "%.1f s".format(ms / 1000)
    val steps = v.optJSONArray("steps")?.objects().orEmpty()
    val most = steps.maxOfOrNull { it.optDouble("ms", 0.0) }?.takeIf { it > 0 } ?: 1.0
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("Typical ${s(v.optDouble("total_ms"))} · slow ones ${s(v.optDouble("total_p95_ms"))} · ${v.optInt("utterances")} times",
            color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        steps.forEach { st ->
            val ms = st.optDouble("ms", Double.NaN)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(st.optString("step"), color = Bright, fontSize = 13.sp, modifier = Modifier.weight(1f))
                Text(s(ms), color = if (st.optString("step") == v.optString("slowest")) Pal.warn else Bright, fontSize = 13.sp)
            }
            Box(Modifier.fillMaxWidth().height(4.dp).background(BarTrack, RoundedCornerShape(2.dp))) {
                Box(Modifier.fillMaxWidth(if (ms.isNaN()) 0.01f else (ms / most).toFloat().coerceIn(0.01f, 1f))
                    .height(4.dp).background(Bar, RoundedCornerShape(2.dp)))
            }
        }
        val engines = v.optJSONObject("engines")
        if (engines != null && engines.length() > 0) {
            Text("Speech to text: " + engines.keys().asSequence().joinToString(" · ") { e ->
                val x = engines.getJSONObject(e)
                "$e ${s(x.optDouble("stt_ms"))} (${x.optInt("utterances")}×)"
            }, color = Dim, fontSize = 12.sp)
        }
        val missed = v.optJSONObject("missed")
        if (missed != null && missed.length() > 0) {
            Text("Missed: " + missed.keys().asSequence().joinToString(" · ") { k ->
                "${mapOf("timeout" to "no answer", "nothing" to "no words", "unclear" to "too unclear",
                    "not_you" to "not your voice")[k] ?: k} ${missed.optInt(k)}"
            }, color = Pal.warn, fontSize = 12.sp)
        }
        if (v.optString("slowest").isNotBlank()) {
            Text("Slowest step: ${v.optString("slowest")}", color = Dim, fontSize = 12.sp)
        }
    }
}

/** One model: requests, input and output tokens, and each kind of request that went to it. */
@Composable
private fun ModelTokens(m: JSONObject, picked: String, onPick: (String) -> Unit) {
    val free = m.optString("provider") != "claude"
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(m.optString("model"), color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            Text(if (free) "free" else usd(m.optDouble("cost_usd")), color = Pal.text, fontSize = 15.sp)
        }
        Text(m.optString("provider"), color = Dim, fontSize = 12.sp)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("Requests" to m.optInt("requests").toString(),
                "Input" to tokens(m.optLong("input_tokens")),
                "Output" to tokens(m.optLong("output_tokens")),
                "Cached" to tokens(m.optLong("cache_read_tokens"))).forEach { (label, value) ->
                Column(Modifier.weight(1f).background(BarTrack, RoundedCornerShape(8.dp)).padding(8.dp)) {
                    Text(label, color = Dim, fontSize = 11.sp)
                    Text(value, color = Pal.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
        val kinds = m.optJSONArray("purposes")?.objects().orEmpty()
        val most = kinds.maxOfOrNull { it.optInt("requests") }?.takeIf { it > 0 } ?: 1
        kinds.forEach { k ->
            Column(Modifier.fillMaxWidth()
                .background(if (picked == k.optString("name")) BarTrack else Color.Transparent, RoundedCornerShape(8.dp))
                .clickable { onPick(k.optString("name")) }.padding(vertical = 4.dp, horizontal = 4.dp)) {
                Row {
                    Text(k.optString("label"), color = Bright, fontSize = 13.sp, modifier = Modifier.weight(1f))
                    Text("${k.optInt("requests")} req", color = Bright, fontSize = 13.sp)
                }
                Box(Modifier.fillMaxWidth().padding(vertical = 3.dp).height(4.dp).background(BarTrack, RoundedCornerShape(2.dp))) {
                    Box(Modifier.fillMaxWidth((k.optInt("requests").toFloat() / most).coerceIn(0.02f, 1f)).height(4.dp)
                        .background(Bar, RoundedCornerShape(2.dp)))
                }
                Text("${tokens(k.optLong("input_tokens"))} in · ${tokens(k.optLong("output_tokens"))} out" +
                    (if (k.optLong("cache_read_tokens") > 0) " · ${tokens(k.optLong("cache_read_tokens"))} cached" else "") +
                    (if (!free && k.optDouble("cost_usd") > 0) " · ${usd(k.optDouble("cost_usd"))}" else ""),
                    color = Dim, fontSize = 11.sp)
            }
        }
    }
}

@Composable
private fun Chip(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label, color = if (on) Pal.onChip else Bright, fontSize = 13.sp,
        modifier = Modifier
            .background(if (on) Bar else Card, RoundedCornerShape(50))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

@Composable
private fun Heading(text: String) {
    Text(text, color = Pal.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun Tile(label: String, value: String, modifier: Modifier, note: String = "") {
    Column(modifier.background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Text(label, color = Dim, fontSize = 12.sp)
        Text(value, color = Pal.text, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
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
                                if (i == picked) Pal.text else Bar,
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
            Text(title, color = Pal.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
            Text(value, color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
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
            Text(c.optString("purpose_label"), color = Pal.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
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
                val verdict = mapOf("used" to "Claude used it as given", "fixed" to "Claude fixed it",
                    "redone" to "Claude redid it", "failed_check" to "failed the auto-check")[c.optString("verdict")]
                if (verdict != null) append("\nReview: $verdict")
                if (!c.isNull("rating")) append(if (c.optInt("rating") > 0) "\nYou: \uD83D\uDC4D" else "\nYou: \uD83D\uDC4E")
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


/** One service in "What each service costs": month so far, the forecast, how it's measured and why. */
@Composable
private fun ServiceCost(c: JSONObject, share: Float) {
    val source = c.optString("source")
    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(c.optString("provider"), color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            Text(usd(c.optDouble("month_to_date")), color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }
        Box(Modifier.fillMaxWidth().padding(vertical = 6.dp).height(6.dp).background(BarTrack, RoundedCornerShape(3.dp))) {
            Box(Modifier.fillMaxWidth(share.coerceIn(0.01f, 1f)).height(6.dp).background(Bar, RoundedCornerShape(3.dp)))
        }
        Text("Heading for ${usd(c.optDouble("forecast"))} this month · $source", color =
            if (source == "price not set") Pal.warn else Dim, fontSize = 12.sp)
        c.optString("note").takeIf { it.isNotBlank() }?.let { Text(it, color = Dim, fontSize = 12.sp) }
    }
}


/** The Claude cutoff (Claude stops for the month when reached) and the overall monthly budget, editable. */
@Composable
private fun BudgetCard(b: JSONObject, link: ServerLink?, onChanged: (JSONObject) -> Unit) {
    val scope = rememberCoroutineScope()
    var message by remember { mutableStateOf<String?>(null) }
    val claude = b.optJSONObject("claude") ?: JSONObject()
    val all = b.optJSONObject("all_in") ?: JSONObject()
    var claudeText by remember(b) { mutableStateOf(if (claude.isNull("cutoff_usd")) "" else "%.0f".format(claude.optDouble("cutoff_usd"))) }
    var allText by remember(b) { mutableStateOf(if (all.isNull("budget_usd")) "" else "%.0f".format(all.optDouble("budget_usd"))) }

    fun startFresh() {
        if (link == null) return
        scope.launch {
            runCatching { InsightsApi.fresh(link) }
                .onSuccess { message = it.optString("said"); onChanged(it) }
                .onFailure { message = "Couldn't save (${it.message})." }
        }
    }

    fun answer(approve: Boolean) {
        if (link == null) return
        scope.launch {
            runCatching { InsightsApi.pace(link, approve) }
                .onSuccess { message = it.optString("said"); onChanged(it) }
                .onFailure { message = "Couldn't save (${it.message})." }
        }
    }

    fun save(which: String, text: String) {
        val usd = text.trim().ifEmpty { "0" }.toDoubleOrNull()
        if (usd == null || usd < 0) { message = "Give an amount in dollars (0 turns it off)."; return }
        if (link == null) return
        scope.launch {
            runCatching { InsightsApi.setBudget(link, which, usd) }
                .onSuccess { message = it.optString("said"); onChanged(it) }
                .onFailure { message = "Couldn't save (${it.message})." }
        }
    }

    @Composable
    fun Meter(used: Double, cap: Double) {
        val share = if (cap > 0) (used / cap).coerceIn(0.0, 1.0).toFloat() else 0f
        val colour = when { share >= 1f -> Pal.bad; share >= 0.8f -> Pal.warn; else -> Bar }
        Box(Modifier.fillMaxWidth().height(8.dp).background(BarTrack, RoundedCornerShape(4.dp))) {
            Box(Modifier.fillMaxWidth(share).fillMaxHeight().background(colour, RoundedCornerShape(4.dp)))
        }
    }

    Column(Modifier.fillMaxWidth().background(Card, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Budgets this month", color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        b.optString("counting_from").takeIf { it.length >= 16 && it != "null" }?.let {
            Text("Counting from ${it.substring(0, 10)} ${it.substring(11, 16)} UTC (earlier spending this month let go)",
                 color = Dim, fontSize = 11.sp)
        }

        val prediction = b.optJSONObject("prediction")
        prediction?.optJSONArray("limits")?.objects()?.forEach { l ->
            val name = if (l.optString("which") == "claude") "Claude" else "Whole Buddy"
            val reach = l.optString("reach_on").takeIf { it.isNotEmpty() && it != "null" }
            Text("Where it's going: $name ${usd(l.optDouble("forecast_usd"))} by the month's end, budget ${usd(l.optDouble("cap_usd"))}" +
                 (reach?.let { " · reached around $it" } ?: ""),
                 color = if (l.optBoolean("over")) Pal.bad else Dim, fontSize = 12.sp)
        }
        prediction?.optJSONArray("drivers")?.objects()?.takeIf { it.isNotEmpty() }?.let { d ->
            Text("Last 3 days: " + d.joinToString(", ") { "${it.optString("what").replace('_', ' ')} ${usd(it.optDouble("usd"))}" },
                 color = Dim, fontSize = 11.sp)
        }
        if (b.optBoolean("held")) {
            Text("Background paid work is paused until you approve this pace. Your chat still works.",
                 color = Pal.warn, fontSize = 12.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Chip("Approve the pace", false) { answer(true) }
                Chip("Stay in saver", false) { answer(false) }
            }
        }

        val spent = claude.optDouble("spent_usd", 0.0)
        val cap = if (claude.isNull("cutoff_usd")) 0.0 else claude.optDouble("cutoff_usd")
        Text(if (cap > 0) "Claude: ${usd(spent)} of ${usd(cap)}" + (if (claude.optBoolean("stopped")) " · stopped until the 1st" else "")
             else "Claude: ${usd(spent)} · no cutoff", color = if (claude.optBoolean("stopped")) Pal.bad else Pal.text, fontSize = 13.sp)
        if (cap > 0) Meter(spent, cap)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(claudeText, { claudeText = it.filter { c -> c.isDigit() || c == '.' }.take(7) },
                label = { Text("Claude cutoff $") }, singleLine = true, modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            Chip("Save", false) { save("claude", claudeText) }
        }

        val counted = all.optDouble("counted_usd", 0.0)
        val budget = if (all.isNull("budget_usd")) 0.0 else all.optDouble("budget_usd")
        val allStopped = all.optBoolean("stopped")
        val oldServer = !all.has("counted_usd")  // a server from before the whole-Buddy switch
        Text(when {
            oldServer -> "Whole Buddy: your server needs updating to show this and enforce the switch " +
                "(cd /opt/buddy && sudo git pull && sudo systemctl restart buddy)"
            budget > 0 -> "Whole Buddy: ${usd(counted)} of ${usd(budget)}" +
                (if (allStopped) " · paid services stopped until the 1st" else " · ${usd(all.optDouble("left_usd", 0.0))} left")
            else -> "Whole Buddy: no budget set (no switch)"
        }, color = if (allStopped) Pal.bad else Pal.text, fontSize = 13.sp)
        if (budget > 0 && !oldServer) {
            Meter(counted, budget)
            Text("${usd(all.optDouble("committed_usd", 0.0))} committed (server, plans, fixed) + " +
                 "${usd(all.optDouble("pay_per_use_usd", 0.0))} pay-per-use (Claude, AI models, search, travel prices)" +
                 (if (all.optBoolean("heading_over")) " · heading over at this pace" else ""), color = Dim, fontSize = 11.sp)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(allText, { allText = it.filter { c -> c.isDigit() || c == '.' }.take(7) },
                label = { Text("Overall budget $") }, singleLine = true, modifier = Modifier.weight(1f),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal))
            Chip("Save", false) { save("all_in", allText) }
        }
        Text("Alerts at 50%, 80% and 100%. At the Claude cutoff, Claude stops; at the whole-Buddy budget, Claude and " +
             "every paid service stop, until the 1st. Reminders, the bank feed and your screens keep working.",
             color = Dim, fontSize = 11.sp)
        Chip("Start fresh from now", false) { startFresh() }
        message?.let { Text(it, color = Dim, fontSize = 12.sp) }
    }
}
