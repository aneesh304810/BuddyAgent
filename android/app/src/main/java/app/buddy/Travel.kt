package app.buddy

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
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
import java.util.concurrent.TimeUnit

private val TDim: Color get() = Pal.dim
private val TTile: Color get() = Pal.panel
private val TWarn: Color get() = Pal.warn
private val TGood: Color get() = Pal.good
private val TTrack: Color get() = Pal.track

/** Buddy's travel engine on the server: /api/travel. */
object TravelApi {
    private val http = OkHttpClient.Builder().callTimeout(90, TimeUnit.SECONDS).build()

    private suspend fun call(link: ServerLink, path: String, body: JSONObject? = null): JSONObject =
        withContext(Dispatchers.IO) {
            val b = Request.Builder().url(link.baseUrl + path).header("Authorization", "Bearer ${link.token}")
            if (body != null) b.post(body.toString().toRequestBody("application/json".toMediaType()))
            http.newCall(b.build()).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (!r.isSuccessful) {
                    error(runCatching { JSONObject(text).optString("detail") }.getOrNull()?.ifBlank { null }
                        ?: "server said ${r.code}")
                }
                JSONObject(text)
            }
        }

    suspend fun overview(link: ServerLink) = call(link, "/api/travel")
    suspend fun alerts(link: ServerLink) = call(link, "/api/travel/alerts")
    suspend fun search(link: ServerLink, body: JSONObject) = call(link, "/api/travel/search", body)
    suspend fun get(link: ServerLink, id: String) = call(link, "/api/travel/searches/$id")
    suspend fun refresh(link: ServerLink, sid: String, oid: String) =
        call(link, "/api/travel/searches/$sid/offers/$oid/refresh", JSONObject())
    suspend fun chose(link: ServerLink, sid: String, oid: String) =
        call(link, "/api/travel/searches/$sid/offers/$oid/chose", JSONObject())
    suspend fun profile(link: ServerLink, p: JSONObject) = call(link, "/api/travel/profile", p)
    suspend fun watch(link: ServerLink, w: JSONObject) = call(link, "/api/travel/watches", w)
    suspend fun unwatch(link: ServerLink, id: Int) = withContext(Dispatchers.IO) {
        http.newCall(Request.Builder().url(link.baseUrl + "/api/travel/watches/$id")
            .header("Authorization", "Bearer ${link.token}").delete().build()).execute().close()
    }
}

private fun JSONArray?.objs(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }
private fun JSONArray?.strs(): List<String> = if (this == null) emptyList() else (0 until length()).map { getString(it) }
private fun amount(v: Double, cur: String) = "%,.0f %s".format(v, cur)

@Composable
fun TravelScreen(link: ServerLink?) {
    var overview by remember { mutableStateOf<JSONObject?>(null) }
    var current by remember { mutableStateOf<JSONObject?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(link, refresh) {
        if (link == null) { problem = "Set up your server first."; return@LaunchedEffect }
        runCatching { TravelApi.overview(link) }.onSuccess { overview = it; problem = null }
            .onFailure { problem = "Couldn't load (${it.message})." }
    }
    val cur = current
    if (cur != null && link != null) {
        SearchResults(link, cur, onBack = { current = null; refresh++ })
        return
    }
    LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        problem?.let { item { Text(it, color = Pal.bad) } }
        val o = overview ?: run { if (problem == null) item { Text("Loading…", color = TDim) }; return@LazyColumn }
        item { TransitAlerts(link!!) }
        item {
            SearchForm(busy) { body ->
                busy = true
                scope.launch {
                    runCatching { TravelApi.search(link!!, body) }
                        .onSuccess { current = it; problem = null }
                        .onFailure { problem = "Search failed: ${it.message}" }
                    busy = false
                }
            }
        }
        item { SourcesLine(o.optJSONObject("sources")) }
        item { StylePanel(link!!, o.optJSONObject("profile") ?: JSONObject(), o.optJSONObject("learned")) { refresh++ } }
        item { WatchPanel(link!!, o.optJSONArray("watches").objs(), o.optJSONObject("sources")?.optBoolean("travelpayouts") == true) { refresh++ } }
        val searches = o.optJSONArray("searches").objs()
        if (searches.isNotEmpty()) item { Text("Recent searches", color = TDim, fontSize = 13.sp) }
        items(searches, key = { it.optString("id") }) { s ->
            Column(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(12.dp)).clickable {
                scope.launch { runCatching { TravelApi.get(link!!, s.optString("id")) }.onSuccess { current = it } }
            }.padding(12.dp)) {
                Text(s.optString("title"), color = Pal.text)
                Text("${if (s.optString("kind") == "hotels") "Hotels" else "Flights"} · ${s.optInt("offers")} options · " +
                    s.optString("created_at").take(16).replace('T', ' ') + " UTC", color = TDim, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun SearchForm(busy: Boolean, onSearch: (JSONObject) -> Unit) {
    var hotels by remember { mutableStateOf(false) }
    var from by remember { mutableStateOf("") }
    var to by remember { mutableStateOf("") }
    var depart by remember { mutableStateOf("") }
    var back by remember { mutableStateOf("") }
    var place by remember { mutableStateOf("") }
    var checkIn by remember { mutableStateOf("") }
    var checkOut by remember { mutableStateOf("") }
    var people by remember { mutableStateOf("1") }
    Column(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(false to "✈ Flights", true to "🏨 Hotels").forEach { (h, label) ->
                Text(label, color = if (hotels == h) Pal.onChip else Pal.text,
                    modifier = Modifier.background(if (hotels == h) Pal.chip else TTrack, RoundedCornerShape(50))
                        .clickable { hotels = h; if (h && people == "1") people = "2" }.padding(horizontal = 14.dp, vertical = 6.dp))
            }
        }
        if (!hotels) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(from, { from = it.uppercase().take(3) }, label = { Text("From (MIA)") },
                    singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(to, { to = it.uppercase().take(3) }, label = { Text("To (LIM)") },
                    singleLine = true, modifier = Modifier.weight(1f))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(depart, { depart = it.take(10) }, label = { Text("Depart YYYY-MM-DD") },
                    singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(back, { back = it.take(10) }, label = { Text("Return (optional)") },
                    singleLine = true, modifier = Modifier.weight(1f))
            }
        } else {
            OutlinedTextField(place, { place = it }, label = { Text("Where (Miraflores, Lima)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(checkIn, { checkIn = it.take(10) }, label = { Text("Check-in YYYY-MM-DD") },
                    singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(checkOut, { checkOut = it.take(10) }, label = { Text("Check-out") },
                    singleLine = true, modifier = Modifier.weight(1f))
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(people, { people = it.filter(Char::isDigit).take(1) },
                label = { Text(if (hotels) "Guests" else "Adults") }, singleLine = true, modifier = Modifier.width(110.dp))
            OutlinedButton(onClick = {
                val body = if (hotels) JSONObject().put("kind", "hotels").put("place", place).put("check_in", checkIn)
                    .put("check_out", checkOut).put("adults", people.toIntOrNull() ?: 2)
                else JSONObject().put("kind", "flights").put("origin", from).put("destination", to).put("depart", depart)
                    .put("return", back).put("adults", people.toIntOrNull() ?: 1)
                onSearch(body)
            }, enabled = !busy, modifier = Modifier.weight(1f).padding(top = 6.dp)) {
                Text(if (busy) "Searching all sources…" else "Search")
            }
        }
        Text("Or just ask Buddy: \"flights Miami to Lima on 10 December\".", color = TDim, fontSize = 12.sp)
    }
}

/** Transit alerts: the lines you watch, and what applies where you are (MTA, PATH, NJ Transit, light rail). */
@Composable
private fun TransitAlerts(link: ServerLink) {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var again by remember { mutableIntStateOf(0) }
    var open by remember { mutableStateOf(setOf<String>()) }
    LaunchedEffect(link, again) {
        runCatching { TravelApi.alerts(link) }.onSuccess { data = it; problem = null }
            .onFailure { problem = "Couldn't load travel alerts (${it.message})." }
    }
    Column(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(16.dp)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        val d = data
        Row(Modifier.fillMaxWidth()) {
            Column(Modifier.weight(1f)) {
                Text("Travel alerts", color = Pal.text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                val where = d?.optString("where").orEmpty()
                Text((if (where.isNotEmpty()) "Near $where" else "Your lines") +
                    (d?.optString("checked_at")?.takeIf { it.isNotEmpty() }?.let { " · checked $it" } ?: ""),
                    color = TDim, fontSize = 13.sp)
            }
            Text("↻ Refresh", color = Pal.accent, fontSize = 14.sp,
                modifier = Modifier.clickable { again++ }.padding(6.dp))
        }
        problem?.let { Text(it, color = Pal.bad, fontSize = 13.sp) }
        if (d == null && problem == null) Text("Checking…", color = TDim, fontSize = 13.sp)
        d?.optJSONArray("lines").objs().forEach { line ->
            val alerts = line.optJSONArray("alerts").objs()
            val error = line.optString("error")
            val name = line.optString("line")
            val ok = error.isEmpty() && alerts.isEmpty()
            Column(Modifier.fillMaxWidth().background(Pal.bg, RoundedCornerShape(12.dp))
                .clickable { open = if (name in open) open - name else open + name }.padding(10.dp)) {
                Row {
                    Text(when { error.isNotEmpty() -> "•"; ok -> "✅"; else -> "⚠️" } + "  $name",
                        color = Pal.text, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    Text(when { error.isNotEmpty() -> "not set up"; ok -> "good service"
                        else -> "${alerts.size + line.optInt("more")} alert" + if (alerts.size + line.optInt("more") == 1) "" else "s" },
                        color = if (ok) TGood else if (error.isNotEmpty()) TDim else TWarn, fontSize = 13.sp)
                }
                line.optString("why").takeIf { it.isNotEmpty() }?.let { Text(it, color = TDim, fontSize = 12.sp) }
                val shown = if (name in open) alerts else alerts.take(2)
                shown.forEach { a ->
                    Text("${a.optString("kind")}: ${a.optString("text")}", color = Pal.text, fontSize = 14.sp,
                        maxLines = if (name in open) 12 else 3, modifier = Modifier.padding(top = 4.dp))
                }
                if (alerts.size > shown.size || line.optInt("more") > 0)
                    Text("Tap for ${alerts.size - shown.size + line.optInt("more")} more", color = Pal.accent, fontSize = 12.sp)
                if (error.isNotEmpty()) Text(error, color = TDim, fontSize = 12.sp)
                val trains = line.optJSONArray("next_trains").objs()
                if (trains.isNotEmpty()) Text("Next from ${line.optString("station")}: " +
                    trains.take(4).joinToString(" · ") { "${it.optString("to")} ${it.optString("in")}" },
                    color = TDim, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp))
            }
        }
        d?.optString("note")?.takeIf { it.isNotEmpty() }?.let { Text(it, color = TDim, fontSize = 12.sp) }
    }
}

@Composable
private fun SourcesLine(s: JSONObject?) {
    if (s == null) return
    val parts = listOf(
        "Duffel" to s.optBoolean("duffel"), "Google Flights & Hotels" to s.optBoolean("google"),
        "Travelpayouts" to s.optBoolean("travelpayouts"),
    ).joinToString(" · ") { (n, on) -> if (on) "✓ $n" else "✗ $n" }
    Text(parts + if (s.optBoolean("duffel_test_mode")) "  (Duffel in test mode: pretend prices)" else "",
        color = TDim, fontSize = 12.sp)
}

/** How you travel (bags, seat...) and what Buddy has learned from your choices. */
@Composable
private fun StylePanel(link: ServerLink, p: JSONObject, learned: JSONObject?, onSaved: () -> Unit) {
    var open by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Text((if (open) "▾ " else "▸ ") + "Your travel style", color = Pal.text, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.fillMaxWidth().clickable { open = !open }.padding(vertical = 4.dp))
    if (!open) return
    fun save(key: String, value: Any) = scope.launch {
        runCatching { TravelApi.profile(link, JSONObject().put(key, value)) }; onSaved()
    }
    Column(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        ChoiceRow("Checked bags", listOf("0", "1", "2"), p.optInt("checked_bags").toString()) { save("checked_bags", it.toInt()) }
        ChoiceRow("Carry-on", listOf("yes", "no"), if (p.optBoolean("carry_on", true)) "yes" else "no") { save("carry_on", it == "yes") }
        ChoiceRow("Seat", listOf("any", "window", "aisle"), p.optString("seat", "any")) { save("seat", it) }
        ChoiceRow("Nearby airports", listOf("yes", "no"), if (p.optBoolean("nearby_airports", true)) "yes" else "no") {
            save("nearby_airports", it == "yes")
        }
        ChoiceRow("Hotels: free cancellation only", listOf("yes", "no"), if (p.optBoolean("free_cancellation")) "yes" else "no") {
            save("free_cancellation", it == "yes")
        }
        if (learned != null && learned.optInt("choices") + learned.optInt("hotel_choices") > 0) {
            val bits = mutableListOf<String>()
            if (!learned.isNull("hour_value")) bits += "you pay about ${learned.optDouble("hour_value").toInt()} to save an hour"
            if (!learned.isNull("pays_for_refundable")) bits += "about ${learned.optDouble("pays_for_refundable").toInt()} more for refundable"
            if (!learned.isNull("nonstop_share")) bits += "nonstop ${(learned.optDouble("nonstop_share") * 100).toInt()}% of the time"
            val airlines = learned.optJSONObject("airlines")
            if (airlines != null && airlines.length() > 0) bits += "often ${airlines.keys().asSequence().take(2).joinToString()}"
            Text("Learned from your choices: " + (bits.joinToString("; ").ifBlank { "a few more choices needed" }),
                color = TGood, fontSize = 12.sp)
        }
    }
}

@Composable
private fun ChoiceRow(label: String, options: List<String>, selected: String, onPick: (String) -> Unit) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, color = Pal.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
        options.forEach { o ->
            Text(o, color = if (o == selected) Pal.onChip else TDim, fontSize = 13.sp,
                modifier = Modifier.padding(start = 6.dp).background(if (o == selected) Pal.chip else TTrack, RoundedCornerShape(50))
                    .clickable { onPick(o) }.padding(horizontal = 10.dp, vertical = 4.dp))
        }
    }
}

@Composable
private fun SearchResults(link: ServerLink, search: JSONObject, onBack: () -> Unit) {
    val results = search.optJSONObject("results") ?: JSONObject()
    val hotel = search.optString("kind") == "hotels"
    var sort by remember { mutableStateOf("value") }
    val picked = remember { mutableStateListOf<String>() }
    var comparing by remember { mutableStateOf(false) }
    val offers = results.optJSONArray("offers").objs()
    val sorted = when (sort) {
        "cheapest" -> offers.sortedBy { it.optDouble("true_total") }
        "fastest" -> offers.sortedBy { if (it.optInt("minutes") > 0) it.optInt("minutes") else Int.MAX_VALUE }
        "rated" -> offers.sortedByDescending { it.optDouble("rating") }
        else -> offers
    }
    LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(Modifier.fillMaxWidth()) {
                Text(search.optString("title"), color = Pal.text, fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f))
                Text("New search", color = TDim, modifier = Modifier.clickable { onBack() }.padding(4.dp))
            }
        }
        results.optString("note").takeIf { it.isNotBlank() }?.let { item { Text(it, color = TWarn, fontSize = 13.sp) } }
        results.optJSONArray("problems").strs().forEach { p -> item { Text("Source problem: $p", color = TWarn, fontSize = 12.sp) } }
        val advice = results.optJSONArray("advice").strs()
        if (advice.isNotEmpty()) item {
            Column(Modifier.fillMaxWidth().background(TTrack, RoundedCornerShape(12.dp)).padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Buddy's take", color = Pal.text, fontWeight = FontWeight.SemiBold)
                advice.forEach { Text("• $it", color = Pal.text, fontSize = 13.sp) }
            }
        }
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                (listOf("value" to "Best value", "cheapest" to "Cheapest") +
                    if (hotel) listOf("rated" to "Best rated") else listOf("fastest" to "Fastest")).forEach { (k, l) ->
                    Text(l, color = if (sort == k) Pal.onChip else Pal.text, fontSize = 13.sp,
                        modifier = Modifier.background(if (sort == k) Pal.chip else TTile, RoundedCornerShape(50))
                            .clickable { sort = k }.padding(horizontal = 14.dp, vertical = 6.dp))
                }
                if (picked.size >= 2) {
                    Text("Compare ${picked.size}", color = Pal.onChip, fontSize = 13.sp,
                        modifier = Modifier.background(TGood, RoundedCornerShape(50)).clickable { comparing = !comparing }
                            .padding(horizontal = 14.dp, vertical = 6.dp))
                }
            }
        }
        if (comparing && picked.size >= 2) item { CompareTable(offers.filter { it.optString("id") in picked }, hotel) }
        if (offers.isEmpty()) item { Text("Nothing found: check the airport codes and dates.", color = TDim) }
        items(sorted, key = { it.optString("id") }) { o ->
            OfferCard(link, search.optString("id"), o, o.optString("id") in picked) { on ->
                val id = o.optString("id")
                if (on && id !in picked && picked.size < 3) picked.add(id) else if (!on) picked.remove(id)
            }
        }
        val dates = results.optJSONArray("cheapest_dates").objs()
        if (dates.isNotEmpty()) {
            item { Text("Cheapest dates travellers found lately (check live)", color = TDim, fontSize = 13.sp) }
            items(dates, key = { "d" + it.optString("depart") + it.optDouble("price") }) { d -> DateRow(d) }
        }
        val links = results.optJSONObject("links")
        if (links != null) item {
            val ctx = LocalContext.current
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                links.keys().forEach { k ->
                    val url = links.optString(k)
                    if (url.isNotBlank()) OutlinedButton(onClick = {
                        runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
                    }) { Text("Open on ${k.replaceFirstChar { it.uppercase() }}") }
                }
            }
        }
    }
}

@Composable
private fun OfferCard(link: ServerLink, searchId: String, o: JSONObject, picked: Boolean, onPick: (Boolean) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var recheck by remember { mutableStateOf<String?>(null) }
    val cur = o.optString("currency")
    Column(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        val tags = o.optJSONArray("tags").strs()
        if (tags.isNotEmpty()) Text(tags.joinToString(" · ").uppercase(), color = TGood, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
        Row(Modifier.fillMaxWidth()) {
            Column(Modifier.weight(1f)) {
                Text(o.optString("title"), color = Pal.text, fontWeight = FontWeight.SemiBold)
                Text(o.optString("summary"), color = TDim, fontSize = 13.sp)
            }
            Column {
                Text(amount(o.optDouble("true_total"), cur) + if (o.optBoolean("estimated")) "*" else "",
                    color = Pal.text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                if (o.optDouble("true_total") != o.optDouble("price")) {
                    Text("shown ${amount(o.optDouble("price"), cur)}", color = TDim, fontSize = 12.sp)
                }
            }
        }
        val parts = o.optJSONObject("parts")
        if (parts != null && parts.length() > 1) {
            Text(parts.keys().asSequence().joinToString(" + ") { "${it}: ${amount(parts.optDouble(it), "")}".trim() },
                color = TDim, fontSize = 12.sp)
        }
        o.optJSONArray("flags").objs().forEach { f ->
            val (mark, color) = when (f.optString("level")) { "warn" -> "⚠" to TWarn; "good" -> "✓" to TGood; else -> "•" to TDim }
            Text("$mark ${f.optString("text")}", color = color, fontSize = 13.sp)
        }
        o.optJSONArray("also").objs().forEach { a ->
            Text("Also on ${a.optString("source")}: ${amount(a.optDouble("price"), cur)}", color = TDim, fontSize = 12.sp)
        }
        recheck?.let { Text(it, color = TWarn, fontSize = 12.sp) }
        if (o.optBoolean("estimated")) Text("* includes estimated bag or seat fees", color = TDim, fontSize = 11.sp)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = {
                scope.launch { runCatching { TravelApi.chose(link, searchId, o.optString("id")) } }  // Buddy learns from it
                runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(o.optString("link")))) }
            }, enabled = o.optString("link").isNotBlank()) { Text("Open to book") }
            if (o.optString("source") == "duffel") {
                OutlinedButton(onClick = {
                    scope.launch {
                        recheck = runCatching { TravelApi.refresh(link, searchId, o.optString("id")) }.fold(
                            { r -> if (r.optBoolean("changed")) "Price now ${amount(r.optDouble("price"), cur)} (was ${amount(r.optDouble("was"), cur)})"
                                   else "Price still ${amount(r.optDouble("price"), cur)}" },
                            { "Couldn't re-check: ${it.message}" })
                    }
                }) { Text("Re-check") }
            }
            Text(if (picked) "☑ Compare" else "☐ Compare", color = Pal.text, fontSize = 13.sp,
                modifier = Modifier.clickable { onPick(!picked) }.padding(top = 12.dp))
        }
    }
}

/** Side by side: what you'd pay, how long, what's included, what could go wrong. */
@Composable
private fun CompareTable(offers: List<JSONObject>, hotel: Boolean) {
    val cur = offers.firstOrNull()?.optString("currency") ?: ""
    val rows: List<Pair<String, (JSONObject) -> String>> = if (hotel) listOf(
        "Total stay" to { o -> amount(o.optDouble("true_total"), cur) },
        "Per night" to { o -> if (o.isNull("per_night")) "?" else amount(o.optDouble("per_night"), cur) },
        "Rating" to { o -> if (o.isNull("rating")) "?" else "${o.optDouble("rating")} (${o.optInt("reviews")})" },
        "Cancellation" to { o -> if (o.optJSONArray("flags").objs().any { it.optString("text").startsWith("Free cancellation") }) "free" else "check" },
        "Catches" to { o -> o.optJSONArray("flags").objs().count { it.optString("level") == "warn" }.toString() },
    ) else listOf(
        "True total" to { o -> amount(o.optDouble("true_total"), cur) + if (o.optBoolean("estimated")) "*" else "" },
        "Shown price" to { o -> amount(o.optDouble("price"), cur) },
        "Time" to { o -> o.optString("summary").split(" · ").getOrNull(1) ?: "" },
        "Stops" to { o -> o.optInt("stops").toString() },
        "Refund" to { o -> o.optJSONObject("refund")?.let { r -> if (!r.optBoolean("known")) "?" else if (r.optBoolean("allowed")) "yes" else "no" } ?: "?" },
        "Changes" to { o -> o.optJSONObject("change")?.let { r -> if (!r.optBoolean("known")) "?" else if (r.optBoolean("allowed")) "yes" else "no" } ?: "?" },
        "Catches" to { o -> o.optJSONArray("flags").objs().count { it.optString("level") == "warn" }.toString() },
    )
    Column(Modifier.fillMaxWidth().background(TTrack, RoundedCornerShape(12.dp)).padding(10.dp)) {
        Row {
            Text("", modifier = Modifier.weight(1f))
            offers.forEach { o ->
                Text((if (hotel) o.optString("name") else o.optString("title")).take(22), color = Pal.text, fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1.3f))
            }
        }
        rows.forEach { (label, value) ->
            Row(Modifier.padding(top = 4.dp)) {
                Text(label, color = TDim, fontSize = 12.sp, modifier = Modifier.weight(1f))
                offers.forEach { o -> Text(value(o), color = Pal.text, fontSize = 12.sp, modifier = Modifier.weight(1.3f)) }
            }
        }
    }
}

@Composable
private fun DateRow(d: JSONObject) {
    val ctx = LocalContext.current
    Row(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(10.dp)).clickable(enabled = d.optString("link").isNotBlank()) {
        runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(d.optString("link")))) }
    }.padding(10.dp)) {
        Text(d.optString("depart") + if (d.optString("return").isNotBlank()) " – ${d.optString("return")}" else "",
            color = Pal.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Text("${d.optString("airline")} · ${if (d.optInt("stops") == 0) "nonstop" else "${d.optInt("stops")} stop"} · " +
            amount(d.optDouble("price"), d.optString("currency")), color = TDim, fontSize = 13.sp)
    }
}


/** Routes Buddy checks every morning for a cheap fare (Travelpayouts), speaking up when one shows. */
@Composable
private fun WatchPanel(link: ServerLink, watches: List<JSONObject>, available: Boolean, onChanged: () -> Unit) {
    var open by remember { mutableStateOf(watches.isNotEmpty()) }
    var from by remember { mutableStateOf("") }
    var to by remember { mutableStateOf("") }
    var month by remember { mutableStateOf("") }
    var target by remember { mutableStateOf("") }
    var note by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    Text((if (open) "▾ " else "▸ ") + "Watched routes (${watches.size})", color = Pal.text, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.fillMaxWidth().clickable { open = !open }.padding(vertical = 4.dp))
    if (!open) return
    Column(Modifier.fillMaxWidth().background(TTile, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (!available) {
            Text("Needs Travelpayouts (BUDDY_TRAVELPAYOUTS_TOKEN on the server).", color = TDim, fontSize = 13.sp)
            return@Column
        }
        Text("Checked every morning. Buddy speaks up when a fare hits your target, drops well below the usual, or hits a new low.",
            color = TDim, fontSize = 12.sp)
        watches.forEach { w ->
            Row(Modifier.fillMaxWidth()) {
                Column(Modifier.weight(1f).clickable(enabled = w.optString("best_link").isNotBlank()) {
                    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(w.optString("best_link")))) }
                }) {
                    Text("${w.optString("origin")} → ${w.optString("destination")} · ${w.optString("month")}" +
                        if (w.optString("return_month").isNotBlank()) " (return ${w.optString("return_month")})" else "",
                        color = Pal.text, fontSize = 14.sp)
                    val best = if (w.isNull("best")) "not checked yet" else
                        "lowest seen ${amount(w.optDouble("best"), w.optString("currency"))} on ${w.optString("best_day")}"
                    val tgt = if (w.isNull("target")) "" else " · target ${amount(w.optDouble("target"), w.optString("currency"))}"
                    Text(best + tgt, color = TDim, fontSize = 12.sp)
                }
                Text("✕", color = TDim, modifier = Modifier.clickable {
                    scope.launch { runCatching { TravelApi.unwatch(link, w.optInt("id")) }; onChanged() }
                }.padding(8.dp))
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(from, { from = it.uppercase().take(3) }, label = { Text("From") }, singleLine = true,
                modifier = Modifier.weight(1f))
            OutlinedTextField(to, { to = it.uppercase().take(3) }, label = { Text("To") }, singleLine = true,
                modifier = Modifier.weight(1f))
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(month, { month = it.take(7) }, label = { Text("Month YYYY-MM") }, singleLine = true,
                modifier = Modifier.weight(1f))
            OutlinedTextField(target, { target = it.filter { c -> c.isDigit() || c == '.' } }, label = { Text("Target (optional)") },
                singleLine = true, modifier = Modifier.weight(1f))
        }
        OutlinedButton(onClick = {
            scope.launch {
                val body = JSONObject().put("origin", from).put("destination", to).put("month", month)
                target.toDoubleOrNull()?.let { body.put("target", it) }
                runCatching { TravelApi.watch(link, body) }
                    .onSuccess { note = "Watching ${from} → ${to}."; from = ""; to = ""; month = ""; target = ""; onChanged() }
                    .onFailure { note = "Couldn't add: ${it.message}" }
            }
        }) { Text("Watch this route") }
        note?.let { Text(it, color = TDim, fontSize = 12.sp) }
    }
}
