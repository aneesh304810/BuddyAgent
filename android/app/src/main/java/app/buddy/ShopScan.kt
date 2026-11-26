package app.buddy

import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/**
 * Groceries mode in the scanner (FoodScanActivity): the camera stays open while you shop. Each barcode shows "Good for
 * me?" from your server (foodverdict.py), with one tap to add it to your groceries (groceries.py), which ticks off the
 * shopping-list item it matches; the list shows under the camera. A product that's "Better to skip" for you shows a
 * better one you scanned before and waits for "Add anyway". Only the barcode number goes to your server, which looks it
 * up by the number alone; no picture is kept or sent, and no AI model sees any of it.
 */
data class ShopItem(val id: Int, val text: String)

data class ShopVerdict(val scanId: Int, val name: String, val brand: String, val verdict: String, val headline: String,
                       val numbers: String)

data class ShopAdd(val added: Boolean, val already: Boolean, val ticked: String, val warn: String, val better: String,
                   val toBuy: List<ShopItem>)

object GroceryApi {
    private val http = OkHttpClient.Builder().callTimeout(30, TimeUnit.SECONDS).build()

    private suspend fun call(link: ServerLink, path: String, body: JSONObject? = null): JSONObject =
        withContext(Dispatchers.IO) {
            val request = Request.Builder().url(link.baseUrl.trimEnd('/') + path)
                .header("Authorization", "Bearer ${link.token}")
            if (body != null) request.post(body.toString().toRequestBody("application/json".toMediaType()))
            http.newCall(request.build()).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    val detail = runCatching { JSONObject(text).optString("detail") }.getOrNull().orEmpty()
                    error(if (response.code == 401) "Wrong token: check the server link in Settings."
                          else detail.ifBlank { "The server said ${response.code}." })
                }
                JSONObject(text.ifBlank { "{}" })
            }
        }

    private fun items(array: JSONArray?): List<ShopItem> = (0 until (array?.length() ?: 0)).map {
        val o = array!!.getJSONObject(it)
        ShopItem(o.getInt("id"), o.optString("text"))
    }

    private fun one(d: Double): String = if (d >= 10) d.roundToInt().toString() else "%.1f".format(d).removeSuffix(".0")

    suspend fun toBuy(link: ServerLink): List<ShopItem> = items(call(link, "/api/health/groceries").optJSONArray("to_buy"))

    /** True when the phone is at a store the owner marked (scanmeal.where): the scanner opens in Groceries mode. */
    suspend fun atStore(link: ServerLink): Boolean = call(link, "/api/health/where").optString("do") == "verdict"

    suspend fun check(link: ServerLink, code: String): ShopVerdict {
        val foods = call(link, "/api/health/facts?kind=barcode&q=" + URLEncoder.encode(code, "UTF-8")).optJSONArray("foods")
        if (foods == null || foods.length() == 0) {
            error("That barcode isn't in USDA's or Open Food Facts' lists: check it by name on Food → Groceries.")
        }
        val v = call(link, "/api/health/verdict", JSONObject().put("food", foods.getJSONObject(0)).put("keep", true))
        val per = v.optJSONObject("per_serving") ?: JSONObject()
        fun num(key: String) = per.optDouble(key).takeIf { !it.isNaN() }
        val bits = listOfNotNull(
            v.optJSONObject("serving")?.optString("label")?.takeIf { it.isNotBlank() }?.let { "a serving: $it" },
            num("kcal")?.let { "${it.roundToInt()} kcal" },
            num("sat_fat_g")?.let { "${one(it)} g sat fat" },
            num("sodium_mg")?.let { "${it.roundToInt()} mg sodium" },
            num("added_sugar_g")?.let { "${one(it)} g added sugar" },
        )
        return ShopVerdict(v.getInt("id"), v.optString("name"), v.optString("brand"), v.optString("verdict"),
            v.optString("headline"), bits.joinToString(" · "))
    }

    suspend fun keep(link: ServerLink, scanId: Int, anyway: Boolean): ShopAdd {
        val o = call(link, "/api/health/groceries",
            JSONObject().put("action", "keep").put("scan", scanId).put("anyway", anyway))
        val warn = o.optJSONObject("warn")
        val why = warn?.optJSONArray("why")?.let { a -> (0 until a.length()).joinToString(" ") { a.getString(it) } }.orEmpty()
        val better = warn?.optJSONObject("better")?.let { b ->
            b.optString("name") + b.optString("brand").takeIf { it.isNotBlank() }?.let { " ($it)" }.orEmpty()
        }.orEmpty()
        return ShopAdd(o.optBoolean("added"), o.optBoolean("already"), o.optString("ticked"),
            if (warn != null) why.ifBlank { warn.optString("headline") } else "", better, items(o.optJSONArray("to_buy")))
    }
}

/** One shopping trip in the scanner: what was scanned last, what it's judged, the list, how many were added. */
class ShopSession(private val link: ServerLink?, private val scope: CoroutineScope) {
    var toBuy by mutableStateOf<List<ShopItem>>(emptyList())
    var busy by mutableStateOf(false)
    var verdict by mutableStateOf<ShopVerdict?>(null)
    var add by mutableStateOf<ShopAdd?>(null)
    var problem by mutableStateOf("")
    var added by mutableIntStateOf(0)
    private var last = ""
    private var lastAt = 0L

    fun load() {
        val link = link ?: return
        scope.launch { runCatching { GroceryApi.toBuy(link) }.onSuccess { toBuy = it } }
    }

    /** A barcode the camera read (many times a second while it's in view) or typed: checked once. */
    fun scanned(code: String) {
        scope.launch {  // on the main thread, one at a time
            val now = SystemClock.elapsedRealtime()
            if (busy || (code == last && now - lastAt < 8000)) return@launch
            last = code
            lastAt = now
            val link = link ?: run { problem = "Add your Buddy server link in Settings first."; return@launch }
            busy = true
            problem = ""
            add = null
            runCatching { GroceryApi.check(link, code) }
                .onSuccess { verdict = it }
                .onFailure { verdict = null; problem = it.message ?: "Couldn't check it: try again." }
            busy = false
        }
    }

    fun keep(anyway: Boolean) {
        val link = link ?: return
        val v = verdict ?: return
        scope.launch {
            busy = true
            runCatching { GroceryApi.keep(link, v.scanId, anyway) }
                .onSuccess { a -> add = a; if (a.added) { added += 1; toBuy = a.toBuy } }
                .onFailure { problem = it.message ?: "Couldn't add it: try again." }
            busy = false
        }
    }
}

private fun verdictColor(verdict: String): Color = when (verdict) {
    "good" -> Pal.good
    "skip" -> Pal.bad
    else -> Pal.warn
}

/** Under the camera in Groceries mode: the last product's verdict with one tap to add it, then the list. */
@Composable
fun ShopPanel(s: ShopSession, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (s.busy && s.verdict == null) Text("Checking…", color = Pal.dim)
        if (s.problem.isNotEmpty()) Text(s.problem, color = Pal.bad)
        s.verdict?.let { v ->
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Pal.panel).padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(v.headline.substringBefore(":"), color = verdictColor(v.verdict), fontWeight = FontWeight.SemiBold)
                Text(v.name + if (v.brand.isNotBlank()) " · ${v.brand}" else "", color = Pal.text)
                if (v.numbers.isNotBlank()) Text(v.numbers, color = Pal.dim, fontSize = 13.sp)
                val a = s.add
                when {
                    a == null -> Button(onClick = { s.keep(false) }, enabled = !s.busy, modifier = Modifier.fillMaxWidth()) {
                        Text("Add to my groceries")
                    }
                    a.added -> Text((if (a.already) "✓ One more in your groceries" else "✓ In your groceries") +
                        if (a.ticked.isNotBlank()) " · ticked off ${a.ticked}" else "", color = Pal.good)
                    else -> {
                        Text("Better to skip for you: ${a.warn}", color = Pal.bad)
                        if (a.better.isNotBlank()) Text("Better for you, scanned before: ${a.better}", color = Pal.text)
                        OutlinedButton(onClick = { s.keep(true) }, enabled = !s.busy) { Text("Add anyway") }
                    }
                }
            }
        }
        Text(if (s.toBuy.isEmpty()) "Nothing left on your shopping list."
             else "To buy: " + s.toBuy.joinToString(" · ") { it.text }, color = Pal.text)
        if (s.added > 0) Text("Added this trip: ${s.added}. They're in My kitchen on Food → Groceries.",
            color = Pal.dim, fontSize = 13.sp)
    }
}
