package app.buddy

import android.content.Context
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Buddy's navigation: five places in a bar at the bottom, always one tap away.
 *
 *   Buddy          the one conversation, and what needs you today
 *   Money          accounts and bills, spending, cards, investing
 *   Trips & Deals  travel and deals, with the buy-now-or-wait calls
 *   Discover       search (Buddy Search), news, intelligence, the library and reports
 *   More           your health, how Buddy runs (Maestro, Connections, Security) and your setup
 *
 * Alerts open from the bell in the header on every screen. There is one conversation: on the other four
 * places a microphone button talks to the same Buddy, and the reply shows above it and in the conversation.
 * Icons are drawn here as simple line shapes, so they take the theme's colour and need no icon library.
 */
enum class Place(val label: String) {
    BUDDY("Buddy"), MONEY("Money"), TRIPS("Trips & Deals"), DISCOVER("Discover"), MORE("More")
}

/** The screen a place opens. */
fun homeOf(place: Place): Screen = when (place) {
    Place.BUDDY -> Screen.Talk
    Place.MONEY -> Screen.Dashboard
    Place.TRIPS -> Screen.Trips
    Place.DISCOVER -> Screen.Discover
    Place.MORE -> Screen.More
}

/** Where Back goes from a screen: null for the five places themselves. */
fun parentOf(screen: Screen): Screen? = when (screen) {
    Screen.Talk, Screen.Dashboard, Screen.Trips, Screen.Discover, Screen.More -> null
    Screen.Investments -> Screen.Dashboard
    Screen.Travel, Screen.Deals -> Screen.Trips
    Screen.News, Screen.Intelligence, Screen.Library, Screen.Reports -> Screen.Discover
    is Screen.LibraryItem -> Screen.Library
    is Screen.Report -> Screen.Reports
    Screen.Maestro, Screen.Insights, Screen.Connections, Screen.Security, Screen.Settings, Screen.Health -> Screen.More
    Screen.Alerts -> Screen.Talk
    is Screen.Card -> Screen.Talk
}

/** The place a screen belongs to, for the bar's highlight. */
fun placeOf(screen: Screen): Place {
    var at = screen
    while (true) at = parentOf(at) ?: break
    return when (at) {
        Screen.Dashboard -> Place.MONEY
        Screen.Trips -> Place.TRIPS
        Screen.Discover -> Place.DISCOVER
        Screen.More -> Place.MORE
        else -> Place.BUDDY
    }
}

fun titleOf(screen: Screen): String = when (screen) {
    Screen.Talk -> "Buddy"
    Screen.Dashboard -> "Money"
    Screen.Trips -> "Trips & Deals"
    Screen.Discover -> "Discover"
    Screen.More -> "More"
    Screen.Investments -> "Investing"
    Screen.Travel -> "Travel"
    Screen.Deals -> "Deals"
    Screen.News -> "News"
    Screen.Intelligence -> "Intelligence"
    Screen.Library, is Screen.LibraryItem -> "Library"
    Screen.Reports, is Screen.Report -> "Reports"
    Screen.Maestro, Screen.Insights -> "Maestro"
    Screen.Connections -> "Connections"
    Screen.Security -> "Security"
    Screen.Health -> "Health"
    Screen.Settings -> "Settings"
    Screen.Alerts -> "Alerts"
    is Screen.Card -> "Details"
}

// --- icons: line shapes on a 24 by 24 grid ------------------------------------------------------------------
enum class Glyph { BUDDY, MONEY, TRIPS, DISCOVER, MORE, BELL, MIC, STOP, BACK, NEXT, MAESTRO, PLUG, SHIELD, GEAR, PULSE }

@Composable
fun GlyphIcon(glyph: Glyph, color: Color, size: Dp = 24.dp, modifier: Modifier = Modifier) {
    Canvas(modifier.size(size)) { draw(glyph, color) }
}

private fun DrawScope.draw(glyph: Glyph, color: Color) {
    val u = size.minDimension / 24f
    val pen = Stroke(width = 2f * u, cap = StrokeCap.Round, join = StrokeJoin.Round)
    fun at(x: Float, y: Float) = Offset(x * u, y * u)
    fun line(x1: Float, y1: Float, x2: Float, y2: Float) =
        drawLine(color, at(x1, y1), at(x2, y2), strokeWidth = 2f * u, cap = StrokeCap.Round)
    fun box(x: Float, y: Float, w: Float, h: Float, r: Float) =
        drawRoundRect(color, at(x, y), Size(w * u, h * u), CornerRadius(r * u, r * u), style = pen)
    fun shape(vararg points: Float, closed: Boolean = false) {
        val path = Path()
        path.moveTo(points[0] * u, points[1] * u)
        var i = 2
        while (i < points.size) { path.lineTo(points[i] * u, points[i + 1] * u); i += 2 }
        if (closed) path.close()
        drawPath(path, color, style = pen)
    }
    when (glyph) {
        Glyph.BUDDY -> { box(3f, 4f, 18f, 13f, 4f); shape(8f, 17f, 6f, 21f, 12f, 17f) }
        Glyph.MONEY -> { box(3f, 6f, 18f, 13f, 3f); line(3f, 10f, 21f, 10f); line(15f, 15f, 18f, 15f) }
        Glyph.TRIPS -> { box(4f, 8f, 16f, 12f, 2f); shape(9f, 8f, 9f, 5f, 15f, 5f, 15f, 8f); line(4f, 13f, 20f, 13f) }
        Glyph.DISCOVER -> {
            drawCircle(color, 9f * u, at(12f, 12f), style = pen)
            shape(15.5f, 8.5f, 13.5f, 13.5f, 8.5f, 15.5f, 10.5f, 10.5f, closed = true)
        }
        Glyph.MORE -> listOf(5f, 12f, 19f).forEach { drawCircle(color, 1.8f * u, at(it, 12f)) }
        Glyph.BELL -> {
            drawArc(color, 180f, 180f, false, at(6f, 4f), Size(12f * u, 12f * u), style = pen)
            line(6f, 10f, 6f, 16f); line(18f, 10f, 18f, 16f); line(4f, 16f, 20f, 16f); line(10f, 20f, 14f, 20f)
        }
        Glyph.MIC -> {
            box(9f, 3f, 6f, 11f, 3f)
            drawArc(color, 0f, 180f, false, at(5f, 4f), Size(14f * u, 14f * u), style = pen)
            line(12f, 18f, 12f, 21f)
        }
        Glyph.STOP -> drawRoundRect(color, at(7f, 7f), Size(10f * u, 10f * u), CornerRadius(2f * u, 2f * u))
        Glyph.BACK -> shape(15f, 6f, 9f, 12f, 15f, 18f)
        Glyph.NEXT -> shape(9f, 6f, 15f, 12f, 9f, 18f)
        Glyph.MAESTRO -> {
            shape(9f, 17f, 9f, 6f, 19f, 4f, 19f, 15f)
            drawCircle(color, 2.5f * u, at(6.5f, 17.5f), style = pen)
            drawCircle(color, 2.5f * u, at(16.5f, 15.5f), style = pen)
        }
        Glyph.PLUG -> {
            line(9f, 3f, 9f, 7f); line(15f, 3f, 15f, 7f); box(6f, 7f, 12f, 8f, 3f); line(12f, 15f, 12f, 21f)
        }
        Glyph.SHIELD -> shape(12f, 3f, 20f, 6f, 20f, 12f, 12f, 21f, 4f, 12f, 4f, 6f, closed = true)
        Glyph.PULSE -> shape(3f, 12f, 8f, 12f, 10f, 6f, 14f, 18f, 16f, 12f, 21f, 12f)
        Glyph.GEAR -> {
            drawCircle(color, 3f * u, at(12f, 12f), style = pen)
            drawCircle(color, 8f * u, at(12f, 12f), style = pen)
        }
    }
}

private fun glyphOf(place: Place) = when (place) {
    Place.BUDDY -> Glyph.BUDDY
    Place.MONEY -> Glyph.MONEY
    Place.TRIPS -> Glyph.TRIPS
    Place.DISCOVER -> Glyph.DISCOVER
    Place.MORE -> Glyph.MORE
}

// --- the bar, the bell, the back arrow, the microphone ------------------------------------------------------
@Composable
fun BottomBar(current: Place, onPick: (Place) -> Unit) {
    Column(Modifier.fillMaxWidth().background(Pal.panel)) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(Pal.track))
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 6.dp)) {
            Place.entries.forEach { place ->
                val on = place == current
                val tint = if (on) Pal.link else Pal.dim
                Column(
                    Modifier.weight(1f).heightIn(min = 52.dp).clip(RoundedCornerShape(14.dp))
                        .clickable(role = Role.Tab) { onPick(place) }
                        .semantics { contentDescription = place.label + if (on) ", selected" else "" }
                        .padding(vertical = 4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    GlyphIcon(glyphOf(place), tint, 22.dp)
                    Spacer(Modifier.height(3.dp))
                    Text(place.label, color = tint, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
                }
            }
        }
    }
}

/** The same five places as the bar, down the left side, for a wide screen. */
@Composable
fun SideRail(current: Place, onPick: (Place) -> Unit) {
    Row(Modifier.fillMaxHeight()) {
        Column(
            Modifier.fillMaxHeight().width(92.dp).background(Pal.panel).padding(vertical = 12.dp, horizontal = 6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Place.entries.forEach { place ->
                val on = place == current
                val tint = if (on) Pal.link else Pal.dim
                Column(
                    Modifier.fillMaxWidth().heightIn(min = 60.dp).clip(RoundedCornerShape(14.dp))
                        .background(if (on) Pal.track else Pal.panel)
                        .clickable(role = Role.Tab) { onPick(place) }
                        .semantics { contentDescription = place.label + if (on) ", selected" else "" }
                        .padding(vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    GlyphIcon(glyphOf(place), tint, 24.dp)
                    Spacer(Modifier.height(3.dp))
                    Text(place.label, color = tint, fontSize = 11.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        textAlign = TextAlign.Center, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
                }
            }
        }
        Box(Modifier.fillMaxHeight().width(1.dp).background(Pal.track))
    }
}

@Composable
fun RoundIconButton(glyph: Glyph, label: String, onClick: () -> Unit) {
    Box(
        Modifier.size(44.dp).clip(CircleShape).background(Pal.panel)
            .clickable(role = Role.Button, onClick = onClick).semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) { GlyphIcon(glyph, Pal.text, 20.dp) }
}

/** Talk to Buddy from any place: the same conversation. The reply shows above the button and in the conversation. */
@Composable
fun TalkAnywhere(ui: UiState, modifier: Modifier = Modifier) {
    val service = BuddyService.instance
    val last = ui.lines.lastOrNull { it.from == Speaker.BUDDY }?.text.orEmpty()
    val busy = ui.listening || ui.thinking || ui.speaking
    val status = when {
        ui.onCall -> "Paused while you're on a call"
        ui.thinking -> "Got it, thinking…"
        ui.speaking -> last.ifBlank { "Talking…" }
        ui.listening -> "Listening…"
        else -> ""
    }
    val color = when {
        ui.speaking -> Pal.talking
        ui.thinking -> Pal.thinking
        ui.listening -> Pal.listening
        else -> Pal.accent
    }
    Row(modifier, verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.End) {
        if (status.isNotEmpty()) {
            Text(status, color = Pal.text, fontSize = 14.sp, maxLines = 3, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 260.dp).clip(RoundedCornerShape(16.dp)).background(Pal.panel)
                    .padding(horizontal = 14.dp, vertical = 10.dp))
            Spacer(Modifier.width(10.dp))
        }
        Box(
            Modifier.size(56.dp).clip(CircleShape).background(color)
                .clickable(enabled = service != null, role = Role.Button) { service?.toggle() }
                .semantics { contentDescription = if (busy) "Stop" else "Talk to Buddy" },
            contentAlignment = Alignment.Center,
        ) { GlyphIcon(if (ui.listening) Glyph.STOP else Glyph.MIC, Pal.onChip, 24.dp) }
    }
}

// --- the places that are new --------------------------------------------------------------------------------
/** Tabs at the top of a place; the last one used is remembered. */
@Composable
fun PlaceTabs(name: String, tabs: List<String>, content: @Composable (Int) -> Unit) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("place_$name", Context.MODE_PRIVATE) }
    var tab by remember { mutableStateOf(prefs.getInt("tab", 0).coerceIn(0, tabs.lastIndex)) }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            tabs.forEachIndexed { i, label ->
                FilterChip(selected = tab == i, onClick = { tab = i; prefs.edit().putInt("tab", i).apply() },
                    label = { Text(label) })
            }
        }
        Box(Modifier.fillMaxSize()) {
            key(tab) { content(tab) }  // a fresh page per tab: each WebView loads its own address once
        }
    }
}

@Composable
fun TripsScreen(link: ServerLink?) {
    PlaceTabs("trips", listOf("Trips", "Deals")) { tab ->
        if (tab == 0) TravelScreen(link) else BuddyPage(link, "deals.html")
    }
}

@Composable
fun DiscoverScreen(link: ServerLink?, onNavigate: (Screen) -> Unit) {
    // "discover3": the Search tab is gone (Buddy answers searches and place questions in chat), so the tab
    // remembered under the old order starts afresh.
    PlaceTabs("discover3", listOf("News", "Intelligence", "Library", "Reports")) { tab ->
        when (tab) {
            0 -> BuddyPage(link, "news.html")
            1 -> BuddyPage(link, "intel.html")
            2 -> LibraryScreen(link) { onNavigate(Screen.LibraryItem(it)) }
            else -> ReportsScreen(link) { onNavigate(Screen.Report(it)) }
        }
    }
}

@Composable
private fun MoreRow(glyph: Glyph, title: String, about: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 68.dp).clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).background(Pal.track), contentAlignment = Alignment.Center) {
            GlyphIcon(glyph, Pal.link, 20.dp)
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = Pal.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Text(about, color = Pal.dim, fontSize = 13.sp)
        }
        GlyphIcon(Glyph.NEXT, Pal.dim, 20.dp)
    }
}

@Composable
private fun MoreGroup(title: String, rows: @Composable () -> Unit) {
    Text(title, color = Pal.dim, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 14.dp, bottom = 8.dp))
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(Pal.panel)) { rows() }
}

/** Things looked at rarely, so the other four places stay about your day. */
@Composable
fun MoreScreen(onNavigate: (Screen) -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        MoreGroup("YOU") {
            MoreRow(Glyph.PULSE, "Health", "Your ring and phone numbers, your plan, habits and meals") { onNavigate(Screen.Health) }
        }
        MoreGroup("HOW BUDDY RUNS") {
            MoreRow(Glyph.MAESTRO, "Maestro", "AI models, who may see what, and AI spending") { onNavigate(Screen.Maestro) }
            MoreRow(Glyph.PLUG, "Connections", "The outside services Buddy uses, and whether each works") { onNavigate(Screen.Connections) }
            MoreRow(Glyph.SHIELD, "Security", "The daily threat watch: what is open and what is fixed") { onNavigate(Screen.Security) }
        }
        MoreGroup("YOUR SETUP") {
            MoreRow(Glyph.BELL, "Alerts", "What Buddy told you, and what may interrupt you") { onNavigate(Screen.Alerts) }
            MoreRow(Glyph.GEAR, "Settings", "Server link, voice, language and the look") { onNavigate(Screen.Settings) }
        }
        Spacer(Modifier.height(90.dp))  // room for the microphone button
    }
}
