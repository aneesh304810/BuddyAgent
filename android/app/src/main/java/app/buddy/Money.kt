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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
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
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

private val MDim = Color(0xFF8A93B2)
private val MTile = Color(0xFF151C33)
private val MWarn = Color(0xFFFFC46B)
private val MGood = Color(0xFF7BE0A5)

/** Buddy's finance module on the server: /api/finance. */
object FinanceApi {
    private val http = OkHttpClient.Builder().callTimeout(30, TimeUnit.SECONDS).build()

    private suspend fun call(link: ServerLink, path: String, post: Boolean = false): JSONObject =
        withContext(Dispatchers.IO) {
            val builder = Request.Builder().url(link.baseUrl + path).header("Authorization", "Bearer ${link.token}")
            if (post) builder.post(ByteArray(0).toRequestBody())
            http.newCall(builder.build()).execute().use { r ->
                val body = r.body?.string().orEmpty()
                if (!r.isSuccessful) {
                    error(runCatching { JSONObject(body).optString("detail") }.getOrNull()?.ifBlank { null }
                        ?: "server said ${r.code}")
                }
                JSONObject(body)
            }
        }

    suspend fun overview(link: ServerLink) = call(link, "/api/finance")
    suspend fun decide(link: ServerLink, id: Int, status: String) =
        call(link, "/api/finance/subscriptions/$id?status=$status", post = true)
    suspend fun cancelPage(link: ServerLink, id: Int) = call(link, "/api/finance/subscriptions/$id/cancel")
}

private fun JSONArray.list(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

private fun money(v: Double, currency: String) = "%,.2f %s".format(v, currency)

@Composable
fun MoneyScreen(link: ServerLink?, onReports: () -> Unit = {}) {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var filter by remember { mutableStateOf("all") }
    var refresh by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    LaunchedEffect(link, refresh) {
        if (link == null) {
            problem = "Set up your server first."
            return@LaunchedEffect
        }
        runCatching { FinanceApi.overview(link) }
            .onSuccess { data = it; problem = null }
            .onFailure { problem = "Couldn't load (${it.message})." }
    }
    val d = data
    LazyColumn(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        problem?.let { item { Text(it, color = Color(0xFFFF8A80)) } }
        if (d == null) {
            if (problem == null) item { Text("Loading…", color = MDim) }
            return@LazyColumn
        }
        val totals = d.optJSONObject("totals")
        item {
            Column(Modifier.fillMaxWidth().background(MTile, RoundedCornerShape(12.dp)).padding(14.dp)) {
                Text("Subscriptions", color = MDim, fontSize = 13.sp)
                if (totals == null || totals.length() == 0) {
                    Text("None found yet. Buddy finds them in your card charges: card emails, the bank feed, " +
                        "or card statements shared to Buddy.", color = Color.White, fontSize = 14.sp)
                } else {
                    totals.keys().forEach { cur ->
                        val t = totals.getJSONObject(cur)
                        Text("${money(t.optDouble("monthly"), cur)} a month", color = Color.White, fontSize = 22.sp,
                            fontWeight = FontWeight.SemiBold)
                        Text("${money(t.optDouble("yearly"), cur)} a year · ${t.optInt("count")} subscriptions",
                            color = MDim, fontSize = 13.sp)
                    }
                }
            }
        }
        item {
            Text("Weekly money reports are in Reports →", color = MDim, fontSize = 13.sp,
                modifier = Modifier.clickable { onReports() }.padding(vertical = 2.dp))
        }
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("all" to "All", "decide" to "To decide", "keep" to "Keep", "cancel" to "Cancel").forEach { (k, label) ->
                    Text(label, color = if (filter == k) Color.Black else Color.White, fontSize = 13.sp,
                        modifier = Modifier.background(if (filter == k) Color.White else MTile, RoundedCornerShape(50))
                            .clickable { filter = k }.padding(horizontal = 14.dp, vertical = 6.dp))
                }
            }
        }
        val subs = d.optJSONArray("subscriptions")?.list().orEmpty().filter {
            when (filter) {
                "decide" -> it.optString("status") in listOf("new", "unsure")
                "keep" -> it.optString("status") == "keep"
                "cancel" -> it.optString("status") in listOf("cancel", "cancelled")
                else -> true
            }
        }
        items(subs, key = { it.optInt("id") }) { s ->
            SubscriptionRow(s,
                onDecide = { status ->
                    scope.launch {
                        runCatching { FinanceApi.decide(link!!, s.optInt("id"), status) }
                        if (status == "cancel") {
                            val url = runCatching { FinanceApi.cancelPage(link!!, s.optInt("id")).optString("url") }
                                .getOrNull().orEmpty()
                            if (url.isNotBlank()) {
                                runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
                            } else {
                                BuddyState.add(Line("Marked ${s.optString("merchant")} to cancel. Ask Buddy how to " +
                                    "cancel it; you finish it in their app or site.", Speaker.BUDDY))
                            }
                        }
                        refresh++
                    }
                })
        }
        val cards = d.optJSONArray("cards")?.list().orEmpty()
        item { Text("Cards", color = MDim, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp)) }
        if (cards.isEmpty()) {
            item {
                Text("No cards yet. They come from card statement emails or statements shared to Buddy, or tell " +
                    "Buddy a card's name, last four digits and due day.", color = MDim, fontSize = 13.sp)
            }
        }
        items(cards, key = { "card" + it.optInt("id") }) { c -> CardRow(c) }
    }
}

@Composable
private fun SubscriptionRow(s: JSONObject, onDecide: (String) -> Unit) {
    val status = s.optString("status")
    Column(Modifier.fillMaxWidth().background(MTile, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth()) {
            Text(s.optString("merchant"), color = Color.White, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            Text("${money(s.optDouble("amount"), s.optString("currency"))} / ${s.optString("cadence")}",
                color = Color.White)
        }
        val cards = s.optJSONArray("cards")?.let { a -> (0 until a.length()).map { "••" + a.getString(it) } }.orEmpty()
        Text("Next ${s.optString("next_charge")} · ${money(s.optDouble("yearly_cost"), s.optString("currency"))} a year" +
            if (cards.isNotEmpty()) " · ${cards.joinToString(", ")}" else "", color = MDim, fontSize = 13.sp)
        val flags = s.optJSONArray("flags")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
        if (flags.isNotEmpty()) Text(flags.joinToString(" · "), color = MWarn, fontSize = 13.sp)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("keep" to "Keep", "cancel" to "Cancel", "unsure" to "Not sure").forEach { (k, label) ->
                OutlinedButton(onClick = { onDecide(k) }) {
                    Text(label, color = when {
                        status == k && k == "keep" -> MGood
                        status == k && k == "cancel" -> MWarn
                        status == k -> Color.White
                        else -> MDim
                    })
                }
            }
        }
        if (status == "cancelled") Text("Cancelled: no charge since.", color = MGood, fontSize = 13.sp)
    }
}

@Composable
private fun CardRow(c: JSONObject) {
    val cur = c.optString("currency")
    Column(Modifier.fillMaxWidth().background(MTile, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(3.dp)) {
        Text(c.optString("name") + if (c.optString("last4").isNotBlank()) " ••${c.optString("last4")}" else "",
            color = Color.White, fontWeight = FontWeight.SemiBold)
        val due = c.optString("next_due")
        Text(if (due.isNotBlank()) "Due $due" else "Due date not known yet", color = Color.White, fontSize = 14.sp)
        val parts = mutableListOf<String>()
        if (!c.isNull("statement_balance")) parts += "balance ${money(c.optDouble("statement_balance"), cur)}"
        if (!c.isNull("minimum_due")) parts += "minimum ${money(c.optDouble("minimum_due"), cur)}"
        val used = if (c.has("used_now")) c.optInt("used_now") else if (!c.isNull("used_percent")) c.optInt("used_percent") else null
        if (c.has("balance_now")) parts += "owes ${money(c.optDouble("balance_now"), cur)} now"
        if (used != null) parts += "$used% of limit used"
        if (!c.isNull("apr") && c.optDouble("apr") > 0) parts += "APR ${c.optDouble("apr")}%"
        if (c.optString("next_closing").isNotBlank()) parts += "closes ${c.optString("next_closing")}"
        if (parts.isNotEmpty()) Text(parts.joinToString(" · "), color = MDim, fontSize = 13.sp)
        val pay = c.optDouble("pay_to_get_under_30", 0.0)
        if (used != null && used >= 30) {
            Text(if (pay > 0) "Pay about ${money(pay, cur)} before it closes to report under 30% (helps your credit score)."
                else "Over 30% used: paying some before the closing date helps your credit score.",
                color = MWarn, fontSize = 13.sp)
        }
    }
}
