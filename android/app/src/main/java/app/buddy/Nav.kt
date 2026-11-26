package app.buddy

import android.content.Context
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
 * Buddy's navigation: five destinations in a bar at the bottom (a rail on a wide screen), always one tap away.
 * The redesign (buddy-redesign/03-ux):
 *
 *   Home      the one conversation, what needs you today, and Activity (what Buddy sent on its own: the bell)
 *   Health    your numbers, the plan, food and labs; no AI model sees it
 *   Money     Overview (accounts and bills) first, then Spending, Cards, Invest
 *   Explore   News, Deals, Travel, and Research (Intelligence, Library, Reports)
 *   Profile   account and settings, AI and privacy (Maestro), connections and email, security
 *
 * The earlier five places (Buddy, Money, Trips & Deals, Discover, More) stay for one release behind
 * Settings → Layout, so the change can be undone on the phone without an app update ([NavLayout.v2]).
 * There is one conversation: elsewhere a microphone button talks to the same Buddy, and the reply shows above it
 * and in the conversation. Icons are drawn here as line shapes, in the theme's colour, with no icon library.
 */
object NavLayout {
    /** True: Home, Health, Money, Explore, Profile. False: the earlier places. Set from Settings → Layout. */
    var v2 by mutableStateOf(true)
}

enum class Place(val label: String) {
    HOME("Home"), HEALTH("Health"), MONEY("Money"), EXPLORE("Explore"), PROFILE("Profile"),
    BUDDY("Buddy"), TRIPS("Trips & Deals"), DISCOVER("Discover"), MORE("More")
}

/** The five in the bar, in order. */
fun places(): List<Place> =
    if (NavLayout.v2) listOf(Place.HOME, Place.HEALTH, Place.MONEY, Place.EXPLORE, Place.PROFILE)
    else listOf(Place.BUDDY, Place.MONEY, Place.TRIPS, Place.DISCOVER, Place.MORE)

/** The screen a place opens. */
fun homeOf(place: Place): Screen = when (place) {
    Place.HOME, Place.BUDDY -> Screen.Talk
    Place.HEALTH -> Screen.Health
    Place.MONEY -> Screen.Dashboard
    Place.EXPLORE -> Screen.Explore
    Place.PROFILE -> Screen.Profile
    Place.TRIPS -> Screen.Trips
    Place.DISCOVER -> Screen.Discover
    Place.MORE -> Screen.More
}

/** Where Back goes from a screen: null for the five destinations themselves. */
fun parentOf(screen: Screen): Screen? = if (NavLayout.v2) when (screen) {
    Screen.Talk, Screen.Health, Screen.Dashboard, Screen.Explore, Screen.Profile -> null
    Screen.Investments -> Screen.Dashboard
    Screen.Travel, Screen.Deals, Screen.News, Screen.Intelligence, Screen.Library, Screen.Reports -> Screen.Explore
    Screen.Trips, Screen.Discover -> Screen.Explore  // the earlier places, if one is reached from an old link
    is Screen.LibraryItem -> Screen.Library
    is Screen.Report -> Screen.Reports
    Screen.Maestro, Screen.Insights, Screen.Connections, Screen.Security, Screen.Settings, Screen.Email,
    Screen.More -> Screen.Profile
    Screen.Alerts -> Screen.Talk
    is Screen.Card -> Screen.Talk
} else when (screen) {
    Screen.Talk, Screen.Dashboard, Screen.Trips, Screen.Discover, Screen.More -> null
    Screen.Investments, Screen.Email -> Screen.Dashboard
    Screen.Travel, Screen.Deals -> Screen.Trips
    Screen.News, Screen.Intelligence, Screen.Library, Screen.Reports, Screen.Explore -> Screen.Discover
    is Screen.LibraryItem -> Screen.Library
    is Screen.Report -> Screen.Reports
    Screen.Maestro, Screen.Insights, Screen.Connections, Screen.Security, Screen.Settings, Screen.Health,
    Screen.Profile -> Screen.More
    Screen.Alerts -> Screen.Talk
    is Screen.Card -> Screen.Talk
}

/** The place a screen belongs to, for the bar's highlight. */
fun placeOf(screen: Screen): Place {
    var at = screen
    while (true) at = parentOf(at) ?: break
    return when (at) {
        Screen.Dashboard -> Place.MONEY
        Screen.Health -> Place.HEALTH
        Screen.Explore -> Place.EXPLORE
        Screen.Profile -> Place.PROFILE
        Screen.Trips -> Place.TRIPS
        Screen.Discover -> Place.DISCOVER
        Screen.More -> Place.MORE
        else -> if (NavLayout.v2) Place.HOME else Place.BUDDY
    }
}

/** Where Settings returns to when it is done. */
fun settingsParent(): Screen = if (NavLayout.v2) Screen.Profile else Screen.More

fun titleOf(screen: Screen): String = when (screen) {
    Screen.Talk -> "Buddy"
    Screen.Dashboard -> "Money"
    Screen.Trips -> "Trips & Deals"
    Screen.Discover -> "Discover"
    Screen.More -> "More"
    Screen.Explore -> "Explore"
    Screen.Profile -> "Profile"
    Screen.Email -> "Email"
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
enum class Glyph {
    BUDDY, MONEY, TRIPS, DISCOVER, MORE, BELL, MIC, STOP, BACK, NEXT, MAESTRO, PLUG, SHIELD, GEAR, PULSE, PERSON,
    DOC, MAIL, MEAL, SCAN, TAG
}

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
        Glyph.PERSON -> {
            drawCircle(color, 4f * u, at(12f, 8f), style = pen)
            drawArc(color, 180f, 180f, false, at(4f, 15f), Size(16f * u, 12f * u), style = pen)
        }
        Glyph.DOC -> { box(5f, 3f, 14f, 18f, 2f); line(9f, 9f, 15f, 9f); line(9f, 13f, 15f, 13f); line(9f, 17f, 13f, 17f) }
        Glyph.MAIL -> { box(3f, 5f, 18f, 14f, 2f); shape(3.5f, 6.5f, 12f, 13f, 20.5f, 6.5f) }
        Glyph.MEAL -> { shape(5f, 3f, 5f, 8f, 9f, 8f, 9f, 3f); line(7f, 8f, 7f, 21f); shape(17f, 21f, 17f, 3f, 14f, 10f, 17f, 12f) }
        Glyph.SCAN -> {
            shape(3f, 8f, 3f, 3f, 8f, 3f); shape(16f, 3f, 21f, 3f, 21f, 8f)
            shape(21f, 16f, 21f, 21f, 16f, 21f); shape(8f, 21f, 3f, 21f, 3f, 16f)
            line(8f, 8f, 8f, 16f); line(12f, 8f, 12f, 16f); line(16f, 8f, 16f, 16f)
        }
        Glyph.TAG -> {
            shape(3f, 12f, 12f, 3f, 21f, 3f, 21f, 12f, 12f, 21f, closed = true)
            drawCircle(color, 1.5f * u, at(16.5f, 7.5f))
        }
        Glyph.GEAR -> {
            drawCircle(color, 3f * u, at(12f, 12f), style = pen)
            drawCircle(color, 8f * u, at(12f, 12f), style = pen)
        }
    }
}

private fun glyphOf(place: Place) = when (place) {
    Place.HOME, Place.BUDDY -> Glyph.BUDDY
    Place.HEALTH -> Glyph.PULSE
    Place.EXPLORE -> Glyph.DISCOVER
    Place.PROFILE -> Glyph.PERSON
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
            places().forEach { place ->
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
            places().forEach { place ->
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
fun TalkAnywhere(ui: UiState, modifier: Modifier = Modifier, onScan: (() -> Unit)? = null) {
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
        if (onScan != null) {  // Scan food, used most, one tap from every screen (the result opens Health)
            Box(
                Modifier.size(48.dp).clip(CircleShape).background(Pal.panel).border(1.dp, Pal.track, CircleShape)
                    .clickable(role = Role.Button, onClick = onScan).semantics { contentDescription = "Scan food" },
                contentAlignment = Alignment.Center,
            ) { GlyphIcon(Glyph.SCAN, Pal.link, 22.dp) }
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
fun PlaceTabs(name: String, tabs: List<String>, start: Int? = null, content: @Composable (Int) -> Unit) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("place_$name", Context.MODE_PRIVATE) }
    var tab by remember { mutableStateOf((start ?: prefs.getInt("tab", 0)).coerceIn(0, tabs.lastIndex)) }
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

/** Explore: what's happening outside, as sections. Research is a short list, so it never adds a second tab row. */
@Composable
fun ExploreScreen(link: ServerLink?, onNavigate: (Screen) -> Unit) {
    val start = remember { Jump.explore.also { Jump.explore = null } }  // a quick action on Home (Home.kt)
    PlaceTabs("explore", listOf("News", "Deals", "Travel", "Research"), start) { tab ->
        when (tab) {
            0 -> BuddyPage(link, "news.html")
            1 -> BuddyPage(link, "deals.html")
            2 -> TravelScreen(link)
            else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                MoreGroup("RESEARCH") {
                    MoreRow(Glyph.DISCOVER, "Intelligence", "Emerging technologies, where they meet, and the evidence") {
                        onNavigate(Screen.Intelligence)
                    }
                    MoreRow(Glyph.DOC, "Library", "What you gave Buddy to read, and what it learned about you") {
                        onNavigate(Screen.Library)
                    }
                    MoreRow(Glyph.DOC, "Reports", "What Buddy's background jobs found, this week first") {
                        onNavigate(Screen.Reports)
                    }
                }
                Spacer(Modifier.height(90.dp))
            }
        }
    }
}

/** Profile: you, and how Buddy runs. Opened when needed, so the other four stay about your day. */
@Composable
fun ProfileScreen(onNavigate: (Screen) -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        MoreGroup("ACCOUNT") {
            MoreRow(Glyph.GEAR, "Account and settings", "Server link, voice, language, the look and the layout") {
                onNavigate(Screen.Settings)
            }
        }
        MoreGroup("AI AND PRIVACY") {
            MoreRow(Glyph.MAESTRO, "AI and privacy", "AI models, the Privacy Gateway (who may see what), AI spending") {
                onNavigate(Screen.Maestro)
            }
        }
        MoreGroup("CONNECTIONS") {
            MoreRow(Glyph.PLUG, "Connections", "The outside services Buddy uses, and whether each works") {
                onNavigate(Screen.Connections)
            }
            MoreRow(Glyph.MAIL, "Email", "The kinds of email you get, and what Buddy does with each") {
                onNavigate(Screen.Email)
            }
        }
        MoreGroup("SECURITY") {
            MoreRow(Glyph.SHIELD, "Security", "The daily threat watch: what is open and what is fixed") {
                onNavigate(Screen.Security)
            }
        }
        Spacer(Modifier.height(90.dp))  // room for the microphone button
    }
}
