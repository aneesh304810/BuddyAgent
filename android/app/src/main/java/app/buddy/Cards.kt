package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.tan

private val CardBg = Color(0xFF151C33)
private val CardDim = Color(0xFF8A93B2)
private val CardText = Color(0xFFE0E6FF)
private val Star = Color(0xFFFFC46B)
private val Warn = Color(0xFFFF8A80)
private val LinkBlue = Color(0xFF8FB0FF)

private fun JSONObject.str(key: String): String = if (isNull(key)) "" else optString(key)

private fun kindIcon(kind: String): String = when (kind) {
    "place" -> "📍"     // pin
    "stay" -> "🏠"      // house
    "flight" -> "✈️"
    "travel" -> "🧳"    // luggage
    "news" -> "📰"
    "finance" -> "📈"
    "money" -> "💰"
    "deal" -> "🏷️" // tag
    "reddit" -> "💬"
    "image" -> "\uD83D\uDDBC\uFE0F"
    "file" -> "\uD83D\uDCC4"
    "ride" -> "\uD83D\uDE95"  // taxi
    else -> "🔗"         // link
}

private fun ratingLine(c: JSONObject): String {
    if (c.isNull("rating") || !c.has("rating")) return ""
    val count = if (c.isNull("reviews_count")) "" else " (${String.format("%,d", c.optInt("reviews_count"))})"
    return "★ ${c.optDouble("rating")}$count"
}

/** Results under a reply: swipe sideways, tap one for everything about it. */
@Composable
fun CardsRow(json: String, onOpen: (String) -> Unit) {
    val list = runCatching { JSONArray(json) }.getOrNull() ?: return
    val cards = (0 until list.length()).map { list.getJSONObject(it) }
    val uri = LocalUriHandler.current
    val context = LocalContext.current
    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
        items(cards) { c ->
            // A Word document or deck opens straight in its app; everything else opens its full page.
            CardTile(c) {
                when {
                    c.str("kind") == "file" && c.str("url").isNotEmpty() -> uri.openUri(c.str("url"))
                    c.str("kind") == "ride" -> Links.launch(context, RideLink(c.str("app_url"), c.str("url"), c.str("title")))
                    else -> onOpen(c.toString())
                }
            }
        }
    }
}

@Composable
private fun CardTile(c: JSONObject, onClick: () -> Unit) {
    Column(
        Modifier.width(240.dp).clip(RoundedCornerShape(14.dp)).background(CardBg).clickable(onClick = onClick)
    ) {
        val image = c.str("image")
        when {
            image.isNotEmpty() -> AsyncImage(model = image, contentDescription = c.str("title"),
                contentScale = if (c.str("kind") == "image") ContentScale.Fit else ContentScale.Crop,
                modifier = Modifier.fillMaxWidth().height(if (c.str("kind") == "image") 180.dp else 120.dp)
                    .background(if (c.str("kind") == "image") Color.White else Color.Transparent))
            !c.isNull("lat") && c.has("lat") -> MapTile(c.optDouble("lat"), c.optDouble("lng"), Modifier.height(120.dp))
            c.str("kind") == "file" -> Box(Modifier.fillMaxWidth().height(90.dp).background(
                if (c.str("ext") == "pptx") Color(0xFF7A3B1E) else Color(0xFF1E3A7A)), contentAlignment = Alignment.Center) {
                Text(if (c.str("ext") == "pptx") "\uD83D\uDCCA  PowerPoint" else "\uD83D\uDCDD  Word",
                    color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
            }
            else -> Box(Modifier.fillMaxWidth().height(56.dp).background(Color(0xFF1E2747)),
                contentAlignment = Alignment.CenterStart) {
                Text("  ${kindIcon(c.str("kind"))}  ${c.str("subtitle").ifEmpty { c.str("kind") }}",
                    color = CardDim, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Column(Modifier.padding(10.dp)) {
            Text(c.str("title"), color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            val meta = listOf(ratingLine(c), c.str("price")).filter { it.isNotEmpty() }.joinToString("  ·  ")
            if (meta.isNotEmpty()) Text(meta, color = Star, fontSize = 13.sp)
            if (c.str("kind") == "file") Text(c.str("subtitle") + "  \u00B7  tap to open", color = CardDim, fontSize = 12.sp)
            if (image.isNotEmpty() || c.has("lat")) {
                if (c.str("subtitle").isNotEmpty()) Text(c.str("subtitle"), color = CardDim, fontSize = 12.sp, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
            }
            if (c.str("note").isNotEmpty()) Text(c.str("note"), color = CardText, fontSize = 13.sp,
                fontStyle = FontStyle.Italic, maxLines = 2, overflow = TextOverflow.Ellipsis)
            val catches = c.optJSONArray("catches")
            if (c.str("caution").isNotEmpty() || (catches != null && catches.length() > 0)) {
                Text("⚠️ Catches inside", color = Warn, fontSize = 12.sp)
            }
            val rows = c.optJSONArray("rows")
            if (rows != null) (0 until minOf(rows.length(), 3)).forEach { i ->
                val r = rows.getJSONObject(i)
                Text("${r.str("label")}: ${r.str("value")}", color = CardText, fontSize = 12.sp, maxLines = 1)
            }
            if (c.str("text").isNotEmpty() && rows == null) Text(c.str("text"), color = CardDim, fontSize = 12.sp,
                maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** One OpenStreetMap tile with a pin where the place is. */
@Composable
private fun MapTile(lat: Double, lng: Double, modifier: Modifier = Modifier, zoom: Int = 16) {
    val n = 1 shl zoom
    val x = (lng + 180.0) / 360.0 * n
    val latRad = lat * PI / 180.0
    val y = (1.0 - ln(tan(latRad) + 1.0 / cos(latRad)) / PI) / 2.0 * n
    val tileX = floor(x).toInt()
    val tileY = floor(y).toInt()
    val fx = (x - tileX).toFloat()
    val fy = (y - tileY).toFloat()
    BoxWithConstraints(modifier.fillMaxWidth().clip(RoundedCornerShape(0.dp)).background(Color(0xFF1E2747))) {
        // A square tile as wide as the box, shifted so the place sits in the middle row.
        val side = maxWidth
        AsyncImage(model = "https://tile.openstreetmap.org/$zoom/$tileX/$tileY.png", contentDescription = "Map",
            contentScale = ContentScale.FillBounds,
            modifier = Modifier.width(side).aspectRatio(1f).offset(y = maxHeight / 2 - side * fy))
        Box(Modifier.offset(x = side * fx - 7.dp, y = maxHeight / 2 - 7.dp).size(14.dp).background(Warn, CircleShape))
        Text("\u00A9 OpenStreetMap", color = Color.Black.copy(alpha = 0.6f), fontSize = 8.sp,
            modifier = Modifier.align(Alignment.BottomEnd).background(Color.White.copy(alpha = 0.6f)).padding(2.dp))
    }
}

/** Everything about one result: photo, map, reviews with links, the catches, and buttons to open it. */
@Composable
fun CardScreen(json: String) {
    val c = runCatching { JSONObject(json) }.getOrNull() ?: return
    val uri = LocalUriHandler.current
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (c.str("image").isNotEmpty()) {
            val picture = c.str("kind") == "image"
            AsyncImage(model = c.str("image"), contentDescription = c.str("title"),
                contentScale = if (picture) ContentScale.Fit else ContentScale.Crop,
                modifier = Modifier.fillMaxWidth().then(if (picture) Modifier else Modifier.height(200.dp))
                    .clip(RoundedCornerShape(14.dp)).background(if (picture) Color.White else Color.Transparent))
        }
        Text("${kindIcon(c.str("kind"))}  ${c.str("title")}", color = Color.White, fontSize = 20.sp,
            fontWeight = FontWeight.SemiBold)
        val meta = listOf(ratingLine(c), c.str("price"), c.str("subtitle")).filter { it.isNotEmpty() }
        if (meta.isNotEmpty()) Text(meta.joinToString("  ·  "), color = Star, fontSize = 14.sp)
        if (c.str("note").isNotEmpty()) Text("Why: ${c.str("note")}", color = CardText, fontStyle = FontStyle.Italic)

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (c.str("maps_url").isNotEmpty()) Action("Open in Google Maps") { uri.openUri(c.str("maps_url")) }
            if (c.str("url").isNotEmpty()) Action(when (c.str("kind")) {
                "place" -> "Website"; "image" -> "Open full size"; "ride" -> "Open the app"; else -> "Open" }) {
                if (c.str("kind") == "ride") Links.launch(context, RideLink(c.str("app_url"), c.str("url"), c.str("title")))
                else uri.openUri(c.str("url"))
            }
        }
        if (!c.isNull("lat") && c.has("lat")) {
            Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).clickable {
                if (c.str("maps_url").isNotEmpty()) uri.openUri(c.str("maps_url"))
            }) { MapTile(c.optDouble("lat"), c.optDouble("lng"), Modifier.height(180.dp)) }
        }
        if (c.str("address").isNotEmpty()) Text("📍 ${c.str("address")}", color = CardText)
        if (c.str("phone").isNotEmpty()) Text("📞 ${c.str("phone")}", color = CardText)
        c.optJSONObject("hours")?.let { hours ->
            Section("Opening hours")
            hours.keys().forEach { day -> Text("$day: ${hours.optString(day)}", color = CardDim, fontSize = 13.sp) }
        }
        if (c.str("text").isNotEmpty()) Text(c.str("text"), color = CardText)
        c.optJSONArray("rows")?.let { rows ->
            (0 until rows.length()).forEach { i ->
                val r = rows.getJSONObject(i)
                Row(Modifier.fillMaxWidth()) {
                    Text(r.str("label"), color = CardText, modifier = Modifier.weight(1f))
                    Text(r.str("value"), color = Color.White, fontWeight = FontWeight.SemiBold)
                }
            }
        }
        if (c.str("caution").isNotEmpty()) Text("⚠️ ${c.str("caution")}", color = Warn)
        Quotes("The catches (what listings leave out)", c.optJSONArray("catches"), Warn) { uri.openUri(it) }
        Quotes("What reviewers say", c.optJSONArray("reviews"), CardText) { uri.openUri(it) }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun Section(title: String) {
    Text(title, color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 6.dp))
}

@Composable
private fun Action(label: String, onClick: () -> Unit) {
    Text(label, color = Color.Black, fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(RoundedCornerShape(50)).background(Color(0xFF36C5F0)).clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp))
}

@Composable
private fun Quotes(title: String, items: JSONArray?, color: Color, open: (String) -> Unit) {
    if (items == null || items.length() == 0) return
    Section(title)
    (0 until items.length()).forEach { i ->
        val q = items.getJSONObject(i)
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(CardBg)
            .clickable(enabled = q.str("url").isNotEmpty()) { open(q.str("url")) }.padding(10.dp)) {
            Text(q.str("says"), color = color, fontSize = 14.sp)
            Text(q.str("from") + if (q.str("url").isNotEmpty()) "  ·  open ↗" else "", color = LinkBlue, fontSize = 12.sp)
        }
    }
}
