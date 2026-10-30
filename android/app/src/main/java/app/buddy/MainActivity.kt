package app.buddy

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.NotificationManagerCompat

/** Languages people around you might speak, for Listen-in. South America first. */
val ROOM_LANGUAGES = listOf(
    AUTO_LANGUAGE to "Automatic (Buddy works it out)",
    "es-419" to "Spanish (Latin America)",
    "pt-BR" to "Portuguese (Brazil)",
    "es-ES" to "Spanish (Spain)",
    "en-US" to "English",
    "fr-FR" to "French",
    "de-DE" to "German",
    "it-IT" to "Italian",
    "zh-CN" to "Chinese (Mandarin)",
    "ja-JP" to "Japanese",
    "ko-KR" to "Korean",
    "ar-SA" to "Arabic",
    "ru-RU" to "Russian",
    "tl-PH" to "Filipino",
    "kn-IN" to "Kannada",
    "te-IN" to "Telugu",
    "bn-IN" to "Bengali",
    "gu-IN" to "Gujarati",
    "pa-IN" to "Punjabi",
    "ur-IN" to "Urdu",
    "hi-IN" to "Hindi",
    "ml-IN" to "Malayalam",
    "mr-IN" to "Marathi",
    "ta-IN" to "Tamil",
)

private val Ink = Color(0xFF0B1020)
private val Panel = Color(0xFF151C33)
private val Accent = Color(0xFF5B8CFF)
private val Listening = Color(0xFF3DDC97)
private val Talking = Color(0xFFFFB547)
private val Thinking = Color(0xFFB98CFF)  // the question arrived; Buddy is working on it
private val Muted = Color(0xFF8A93B2)

/** What fills the screen below the header. */
sealed interface Screen {
    data object Talk : Screen
    data object Settings : Screen
    data object Reports : Screen
    data object Insights : Screen
    data class Card(val json: String) : Screen
    data class Report(val id: Int) : Screen
}

class MainActivity : ComponentActivity() {
    companion object {
        const val EXTRA_JOB_ID = "app.buddy.JOB_ID"
    }

    private lateinit var prefs: BuddyPrefs
    private val screen = mutableStateOf<Screen>(Screen.Talk)

    private val askPermissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
            if (granted[Manifest.permission.RECORD_AUDIO] != false &&
                hasPermission(Manifest.permission.RECORD_AUDIO)
            ) {
                BuddyService.start(this)
            } else {
                BuddyState.add(Line("Buddy needs the microphone to hear you.", Speaker.ERROR))
            }
        }

    override fun onResume() {
        super.onResume()
        LocationShare.maybeSend(this)
        BuddyService.instance?.claimEarbud() // opening Buddy takes the earbud button back from music apps
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Photos and map tiles in cards; OpenStreetMap asks apps to say who they are.
        coil.Coil.setImageLoader(
            coil.ImageLoader.Builder(this)
                .okHttpClient {
                    okhttp3.OkHttpClient.Builder().addInterceptor { chain ->
                        chain.proceed(chain.request().newBuilder()
                            .header("User-Agent", "Buddy/1.0 (personal assistant app)").build())
                    }.build()
                }
                .crossfade(true)
                .build()
        )
        prefs = BuddyPrefs(this)
        if (prefs.link == null) screen.value = Screen.Settings
        openJobFrom(intent)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Accent, background = Ink, surface = Panel)) {
                BuddyScreen(
                    prefs = prefs,
                    screen = screen.value,
                    onNavigate = { screen.value = it },
                    onSaveLink = { link ->
                        prefs.link = link
                        BuddyService.instance?.reconnect() ?: startBuddy()
                    },
                    onStart = ::startBuddy,
                    onBattery = ::askBatteryExemption,
                    batteryOk = ::batteryOk,
                )
            }
        }
        if (prefs.link != null) startBuddy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openJobFrom(intent)
    }

    private fun openJobFrom(intent: Intent?) {
        val id = intent?.getIntExtra(EXTRA_JOB_ID, -1) ?: -1
        if (id > 0) screen.value = Screen.Report(id)
    }

    private fun startBuddy() {
        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
        }.filterNot(::hasPermission)
        if (needed.isEmpty()) BuddyService.start(this) else askPermissions.launch(needed.toTypedArray())
    }

    private fun hasPermission(permission: String) =
        checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED

    private fun batteryOk(): Boolean =
        getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    /** Samsung puts background apps to sleep; this asks to keep Buddy awake. */
    @SuppressLint("BatteryLife")
    private fun askBatteryExemption() {
        startActivity(
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
        )
    }
}

@Composable
private fun BuddyScreen(
    prefs: BuddyPrefs,
    screen: Screen,
    onNavigate: (Screen) -> Unit,
    onSaveLink: (ServerLink) -> Unit,
    onStart: () -> Unit,
    onBattery: () -> Unit,
    batteryOk: () -> Boolean,
) {
    val ui by BuddyState.ui.collectAsState()

    Column(
        Modifier
            .fillMaxSize()
            .background(Ink)
            .safeDrawingPadding()
            .padding(horizontal = 16.dp),
    ) {
        Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Buddy", color = Color.White, fontSize = 22.sp)
            Spacer(Modifier.size(10.dp))
            Box(
                Modifier.size(8.dp).background(if (ui.online) Listening else Muted, CircleShape)
            )
            Spacer(Modifier.size(6.dp))
            Text(ui.connection, color = Muted, fontSize = 13.sp, modifier = Modifier.weight(1f))
            if (screen == Screen.Talk) {
                TextButton(onClick = { onNavigate(Screen.Insights) }) { Text("Insights") }
                TextButton(onClick = { onNavigate(Screen.Reports) }) { Text("Reports") }
                TextButton(onClick = { onNavigate(Screen.Settings) }) { Text("Settings") }
            } else {
                TextButton(onClick = {
                    onNavigate(if (screen is Screen.Report) Screen.Reports else Screen.Talk)
                }) { Text("Back") }
            }
        }

        when (screen) {
            Screen.Settings -> {
                SettingsPanel(prefs, onSaveLink, onBattery, batteryOk, onDone = { onNavigate(Screen.Talk) })
                return@Column
            }
            Screen.Reports -> {
                ReportsScreen(prefs.link) { onNavigate(Screen.Report(it)) }
                return@Column
            }
            Screen.Insights -> {
                InsightsScreen(prefs.link)
                return@Column
            }
            is Screen.Card -> {
                CardScreen(screen.json)
                return@Column
            }
            is Screen.Report -> {
                ReportScreen(prefs.link, screen.id)
                return@Column
            }
            Screen.Talk -> Unit
        }

        if (!ui.running) {
            Button(onClick = onStart, modifier = Modifier.fillMaxWidth()) { Text("Start Buddy") }
        }

        Transcript(ui.lines, Modifier.weight(1f), { onNavigate(Screen.Insights) }, { onNavigate(Screen.Card(it)) }) {
            onNavigate(Screen.Report(it))
        }
        ui.approval?.let { question -> ApprovalCard(question) }
        Controls(ui, prefs)
    }
}

/** Buddy asked for a yes: one tap answers, like saying "yes" or "no". */
@Composable
private fun ApprovalCard(question: String) {
    Column(
        Modifier.fillMaxWidth().padding(top = 8.dp)
            .background(Panel, RoundedCornerShape(14.dp)).padding(12.dp),
    ) {
        Text(question, color = Color.White, fontSize = 15.sp)
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { BuddyService.instance?.answerApproval(true) }, modifier = Modifier.weight(1f)) {
                Text("Approve")
            }
            OutlinedButton(onClick = { BuddyService.instance?.answerApproval(false) }, modifier = Modifier.weight(1f)) {
                Text("Decline")
            }
        }
    }
}

@Composable
private fun Transcript(
    lines: List<Line>,
    modifier: Modifier,
    onOpenInsights: () -> Unit,
    onOpenCard: (String) -> Unit,
    onOpenReport: (Int) -> Unit,
) {
    val state = rememberLazyListState()
    LaunchedEffect(lines.size, lines.lastOrNull()?.text) {
        if (lines.isNotEmpty()) state.animateScrollToItem(lines.lastIndex)
    }
    if (lines.isEmpty()) {
        Box(modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text("Tap the mic and talk, or type below.\nTry: \"what's on today?\"", color = Muted,
                fontSize = 15.sp, textAlign = TextAlign.Center)
        }
        return
    }
    LazyColumn(modifier.fillMaxWidth(), state = state, verticalArrangement = Arrangement.spacedBy(10.dp),
        contentPadding = PaddingValues(vertical = 8.dp)) {
        itemsIndexed(lines) { i, line ->
            val previous = lines.getOrNull(i - 1)
            // A small time label when the conversation picks up again after a pause.
            if (previous == null || line.at - previous.at > 10 * 60_000) TimeLabel(line.at)
            when (line.from) {
                Speaker.YOU -> UserBubble(line)
                Speaker.STATUS -> StatusLine(line.text)
                Speaker.ERROR -> SystemNote(line.text)
                else -> BuddyBubble(line, onOpenInsights, onOpenReport, onOpenCard)
            }
        }
    }
}

private val TIME = java.text.SimpleDateFormat("EEE HH:mm", java.util.Locale.getDefault())

@Composable
private fun TimeLabel(at: Long) {
    Text(TIME.format(java.util.Date(at)), color = Muted, fontSize = 12.sp, textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth().padding(top = 4.dp))
}

@Composable
private fun UserBubble(line: Line) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        Text(line.text, color = Color.White, fontSize = 16.sp,
            modifier = Modifier.widthIn(max = 520.dp)
                .background(Accent.copy(alpha = 0.45f), RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp))
                .padding(horizontal = 14.dp, vertical = 10.dp))
    }
}

@Composable
private fun StatusLine(text: String) {
    Row(Modifier.padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(Thinking, CircleShape))
        Spacer(Modifier.width(8.dp))
        Text(text, color = Muted, fontSize = 13.sp, fontStyle = FontStyle.Italic)
    }
}

@Composable
private fun SystemNote(text: String) {
    Text("ⓘ  $text", color = Color(0xFFFFA8A0), fontSize = 12.sp, textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp))
}

private fun nudgeLabel(line: Line): String = when {
    line.from == Speaker.WHISPER -> "🤫 Whisper"
    line.opensInsights -> "🩺 Daily check · tap for Insights"
    line.jobId != null && line.kind == "job_done" && line.text.startsWith("Done:") -> "\u2705 Task done \u00B7 tap for the report"
    line.jobId != null -> "📄 Report ready · tap to open"
    line.kind.contains("remind") -> "⏰ Reminder"
    line.kind.contains("brief") || line.kind.contains("morning") -> "☀️ Briefing"
    line.kind.contains("mail") -> "✉️ Email"
    line.kind.contains("price") || line.kind.contains("deal") -> "🏷️ Price watch"
    line.kind.contains("notification") -> "🔔 Notification"
    else -> "💬 Buddy"
}

@Composable
private fun BuddyBubble(line: Line, onOpenInsights: () -> Unit, onOpenReport: (Int) -> Unit, onOpenCard: (String) -> Unit) {
    val special = line.from == Speaker.NUDGE || line.from == Speaker.WHISPER
    Column(Modifier.fillMaxWidth()) {
        if (line.text.isNotEmpty()) {
            Row(verticalAlignment = Alignment.Top) {
                Box(Modifier.padding(top = 2.dp).size(28.dp).background(
                    if (special) Talking.copy(alpha = 0.3f) else Accent.copy(alpha = 0.25f), CircleShape),
                    contentAlignment = Alignment.Center) { Text("B", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold) }
                Spacer(Modifier.width(8.dp))
                Column(
                    Modifier.widthIn(max = 560.dp)
                        .background(Panel, RoundedCornerShape(4.dp, 18.dp, 18.dp, 18.dp))
                        .then(if (line.jobId != null) Modifier.clickable { onOpenReport(line.jobId) } else Modifier)
                        .then(if (line.opensInsights) Modifier.clickable { onOpenInsights() } else Modifier)
                        .padding(horizontal = 14.dp, vertical = 10.dp)
                ) {
                    if (special) Text(nudgeLabel(line), color = Talking, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(bottom = 4.dp))
                    RichText(line.text, italic = line.from == Speaker.WHISPER)
                }
            }
        }
        line.cards?.let { Box(Modifier.padding(start = 36.dp)) { CardsRow(it, onOpenCard) } }
        if (line.from == Speaker.BUDDY && line.turn != null && line.text.isNotEmpty()) {
            Box(Modifier.padding(start = 30.dp)) { Thumbs(line.turn, line.rating) }
        }
    }
}

private val URL = Regex("""https://[^\s)]+""")
private val BOLD = Regex("""\*\*(.+?)\*\*""")

/** Enough formatting for a chat: **bold**, bullet and numbered lists, tappable links. */
@Composable
private fun RichText(text: String, italic: Boolean = false) {
    val blocks = text.lines()
    Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
        blocks.forEach { raw ->
            val bullet = Regex("""^\s*(?:[-*•]|(\d+)[.)])\s+(.*)""").find(raw)
            val body = bullet?.groupValues?.get(2) ?: raw
            if (body.isBlank()) return@forEach
            val styled = buildAnnotatedString {
                var rest = body
                while (rest.isNotEmpty()) {
                    val b = BOLD.find(rest)
                    val u = URL.find(rest)
                    val next = listOfNotNull(b, u).minByOrNull { it.range.first }
                    if (next == null) { append(rest); break }
                    append(rest.substring(0, next.range.first))
                    if (next === b) {
                        withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) { append(next.groupValues[1]) }
                    } else {
                        withLink(LinkAnnotation.Url(next.value, TextLinkStyles(SpanStyle(color = Color(0xFF8FB0FF),
                            textDecoration = TextDecoration.Underline)))) {
                            append(next.value.removePrefix("https://").take(40))
                        }
                    }
                    rest = rest.substring(next.range.last + 1)
                }
            }
            Row {
                if (bullet != null) Text(if (bullet.groupValues[1].isNotEmpty()) "${bullet.groupValues[1]}. " else "•  ",
                    color = Muted, fontSize = 16.sp)
                Text(styled, color = Color.White, fontSize = 16.sp, lineHeight = 22.sp,
                    fontStyle = if (italic) FontStyle.Italic else FontStyle.Normal)
            }
        }
    }
}

/** Was this reply good? Tap again to take it back. */
@Composable
private fun Thumbs(turn: String, rating: Int?) {
    Row(Modifier.padding(start = 6.dp, top = 2.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        listOf(1 to "👍", -1 to "👎").forEach { (value, icon) ->
            val on = rating == value
            Text(
                icon,
                fontSize = 13.sp,
                color = if (rating == null || on) Color.White else Color.White.copy(alpha = 0.3f),
                modifier = Modifier
                    .background(if (on) Accent.copy(alpha = 0.35f) else Color.Transparent, RoundedCornerShape(10.dp))
                    .clickable { BuddyService.instance?.rate(turn, if (on) null else value) }
                    .padding(horizontal = 6.dp, vertical = 2.dp)
                    .then(if (rating == null) Modifier.alpha(0.6f) else Modifier),
            )
        }
    }
}

/** The bottom bar: mode and hands-free on one slim line, then typing and the mic. */
@Composable
private fun Controls(ui: UiState, prefs: BuddyPrefs) {
    val service = BuddyService.instance
    var handsFree by remember { mutableStateOf(prefs.handsFree) }
    var roomLanguage by remember { mutableStateOf(prefs.roomLanguage) }
    var languageMenu by remember { mutableStateOf(false) }
    var typed by remember { mutableStateOf("") }

    val stateColor by animateColorAsState(
        when {
            ui.speaking -> Talking
            ui.thinking -> Thinking
            ui.listening -> Listening
            else -> Accent
        },
        label = "mic",
    )
    val status = when {
        ui.onCall -> "Paused while you're on a call"
        ui.speaking -> "Talking. Speak to interrupt."
        ui.thinking -> "Got it, thinking…"
        ui.listening && ui.mode == Mode.LISTEN_IN && ui.conversationOpen -> "Listening to you, just reply"
        ui.listening && ui.mode == Mode.LISTEN_IN -> "Listening to the room. Say \"Hey Buddy…\""
        ui.listening -> "Listening…"
        ui.mode == Mode.LISTEN_IN -> "Tap the mic to listen to the room"
        else -> "Tap the mic or your earbud to talk"
    }

    Column(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            // Talk | Listen-in, as one small switch.
            Row(Modifier.background(Panel, RoundedCornerShape(50)).padding(3.dp)) {
                listOf(Mode.TALK to "Talk", Mode.LISTEN_IN to "Listen-in").forEach { (mode, label) ->
                    val on = ui.mode == mode
                    Text(label, color = if (on) Color.Black else Color.White, fontSize = 13.sp,
                        fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                        modifier = Modifier.clip(RoundedCornerShape(50))
                            .background(if (on) Color(0xFFE0E6FF) else Color.Transparent)
                            .clickable { switchMode(mode, ui) }
                            .padding(horizontal = 12.dp, vertical = 6.dp))
                }
            }
            Text(status, color = Muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f).padding(horizontal = 10.dp))
            if (ui.mode == Mode.TALK) {
                Text(if (handsFree) "🎧 Hands-free" else "🎧 Tap-to-talk",
                    color = if (handsFree) Listening else Muted, fontSize = 13.sp,
                    modifier = Modifier.clip(RoundedCornerShape(50))
                        .background(if (handsFree) Listening.copy(alpha = 0.15f) else Panel)
                        .clickable { handsFree = !handsFree; prefs.handsFree = handsFree }
                        .padding(horizontal = 10.dp, vertical = 6.dp))
            } else {
                Box {
                    Text("🌐 " + (ROOM_LANGUAGES.firstOrNull { it.first == roomLanguage }?.second ?: roomLanguage),
                        color = Color.White, fontSize = 13.sp,
                        modifier = Modifier.clip(RoundedCornerShape(50)).background(Panel)
                            .clickable { languageMenu = true }.padding(horizontal = 10.dp, vertical = 6.dp))
                    DropdownMenu(expanded = languageMenu, onDismissRequest = { languageMenu = false }) {
                        ROOM_LANGUAGES.forEach { (code, name) ->
                            DropdownMenuItem(text = { Text(name) }, onClick = {
                                roomLanguage = code
                                prefs.roomLanguage = code
                                languageMenu = false
                                if (ui.listening && ui.mode == Mode.LISTEN_IN) service?.startListening(Mode.LISTEN_IN)
                            })
                        }
                    }
                }
            }
        }

        // Typing, and one round button: Send when there's text, the mic otherwise.
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = typed,
                onValueChange = { typed = it },
                placeholder = { Text("Message Buddy") },
                maxLines = 4,
                shape = RoundedCornerShape(24.dp),
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(10.dp))
            val sending = typed.isNotBlank()
            val pulse = rememberInfiniteTransition(label = "pulse")
            val scale by pulse.animateFloat(1f, if (ui.listening && !sending) 1.12f else 1f,
                infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "scale")
            Box(
                Modifier.size(56.dp).scale(scale).background(if (sending) Accent else stateColor, CircleShape)
                    .clickable(enabled = service != null) {
                        if (sending) { service?.sayToBuddy(typed.trim()); typed = "" } else service?.toggle()
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(if (sending) "➤" else if (ui.listening) "■" else "🎙️",
                    color = Color.Black, fontSize = if (sending) 22.sp else 20.sp)
            }
        }
    }
}

private fun switchMode(mode: Mode, ui: UiState) {
    val service = BuddyService.instance
    if (ui.listening && service != null) service.startListening(mode)
    else BuddyState.update { it.copy(mode = mode) }
}

@Composable
private fun SettingsPanel(
    prefs: BuddyPrefs,
    onSaveLink: (ServerLink) -> Unit,
    onBattery: () -> Unit,
    batteryOk: () -> Boolean,
    onDone: () -> Unit,
) {
    val saved = prefs.link
    var linkText by remember { mutableStateOf(saved?.baseUrl ?: "") }
    var tokenText by remember { mutableStateOf("") }
    var problem by remember { mutableStateOf<String?>(null) }
    var earbudMic by remember { mutableStateOf(prefs.earbudMic) }
    var listenMic by remember { mutableStateOf(prefs.listenMic) }
    var earbudsStart by remember { mutableStateOf(prefs.earbudsStart) }
    var speechEngine by remember { mutableStateOf(prefs.speechEngine) }
    var deviceName by remember { mutableStateOf(prefs.deviceName) }
    var shareCalendar by remember { mutableStateOf(prefs.shareCalendar) }
    var shareLocation by remember { mutableStateOf(prefs.shareLocation) }
    var workBusyOnly by remember { mutableStateOf(prefs.workBusyOnly) }
    val context = LocalContext.current
    val notificationsOn = NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)
    val askLocation = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        val granted = result.values.any { it }
        shareLocation = granted
        prefs.shareLocation = granted
        if (granted) LocationShare.maybeSend(context, force = true)
    }
    val askCalendar = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        shareCalendar = granted
        prefs.shareCalendar = granted
        if (granted) BuddyService.instance?.syncCalendar()
    }

    Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Your server", color = Color.White, fontSize = 18.sp)
        Text(
            "Paste the link the server setup printed, like https://buddy.your-tailnet.ts.net/?token=… " +
                "Tailscale must be on for this phone to reach it.",
            color = Muted, fontSize = 14.sp,
        )
        OutlinedTextField(
            value = linkText,
            onValueChange = { linkText = it; problem = null },
            label = { Text("Server link") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = tokenText,
            onValueChange = { tokenText = it; problem = null },
            label = { Text(if (saved != null) "Token (leave empty to keep the saved one)" else "Token, if not in the link") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        problem?.let { Text(it, color = Color(0xFFFF8A80), fontSize = 14.sp) }
        Button(onClick = {
            val link = ServerLink.parse(linkText, tokenText.trim().ifEmpty { saved?.token })
            if (link == null) {
                problem = "That doesn't look right. It must start with https:// and include the token."
            } else {
                onSaveLink(link)
                linkText = link.baseUrl
                tokenText = ""
                onDone()
            }
        }) { Text("Save and connect") }

        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Use the earbud microphone", color = Color.White)
                Text(
                    "For talking to Buddy in a pocket or a noisy place. Earbuds switch to headset mode, " +
                        "so Buddy's voice sounds more like a phone call.",
                    color = Muted, fontSize = 13.sp,
                )
            }
            Switch(checked = earbudMic, onCheckedChange = { earbudMic = it; prefs.earbudMic = it })
        }
        Text("Listen-in microphone", color = Color.White)
        Text(
            "Automatic: the phone's mic, or the earbuds' while the phone is in your pocket. " +
                "Earbud mics favour one nearby voice and switch the earbuds to phone-call sound.",
            color = Muted, fontSize = 13.sp,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("auto" to "Automatic", "phone" to "Phone", "earbuds" to "Earbuds").forEach { (value, name) ->
                FilterChip(
                    selected = listenMic == value,
                    onClick = { listenMic = value; prefs.listenMic = value },
                    label = { Text(name) },
                )
            }
        }

        Spacer(Modifier.height(8.dp))
        Text("Speech recognition", color = Color.White)
        Text(
            "Automatic: Whisper on your server whenever Buddy keeps listening (Talk, Listen-in, phone " +
                "closed): no beeps, good with Malayalam and English. The phone's recognizer for single " +
                "tap-to-talk questions. Phone: the phone's recognizer always (it beeps each time it restarts). " +
                "Server: Whisper always, most private. Whisper needs deploy/whisper.sh on the server.",
            color = Muted, fontSize = 13.sp,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("auto" to "Automatic", "phone" to "Phone", "server" to "Server").forEach { (value, name) ->
                FilterChip(
                    selected = speechEngine == value,
                    onClick = { speechEngine = value; prefs.speechEngine = value },
                    label = { Text(name) },
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Text("When earbuds connect", color = Color.White)
        Text(
            "Listen-in: Buddy starts listening by itself; say \"Hey Buddy\" to talk to it, no tapping. " +
                "Talk: everything you say goes to Buddy, so only for when you're alone.",
            color = Muted, fontSize = 13.sp,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("listen" to "Listen-in", "talk" to "Talk", "off" to "Nothing").forEach { (value, name) ->
                FilterChip(
                    selected = earbudsStart == value,
                    onClick = { earbudsStart = value; prefs.earbudsStart = value },
                    label = { Text(name) },
                )
            }
        }

        Spacer(Modifier.height(8.dp))
        VoicePicker(prefs)
        SpeechTuning(prefs)

        Spacer(Modifier.height(8.dp))
        Text("This phone", color = Color.White, fontSize = 18.sp)
        OutlinedTextField(
            value = deviceName,
            onValueChange = { deviceName = it; prefs.deviceName = it },
            label = { Text("Name, e.g. \"Fold 8\" or \"Work phone\"") },
            placeholder = { Text(android.os.Build.MODEL ?: "phone") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Column {
            Text("Share notifications", color = Color.White)
            Text(
                if (notificationsOn) "On. Buddy sees this phone's notifications (Teams, WhatsApp, bank alerts...). " +
                    "Codes and card numbers are masked; kept 7 days."
                else "Let Buddy see this phone's notifications, so it can tell you about Teams messages, " +
                    "meeting alerts and bank charges. You can switch it off any time.",
                color = Muted, fontSize = 13.sp,
            )
            OutlinedButton(
                onClick = { context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) },
                modifier = Modifier.padding(top = 4.dp),
            ) { Text(if (notificationsOn) "Change notification access" else "Turn on notification access") }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Share my location", color = Color.White)
                Text(
                    "So \"a spa near me\" works. Sent only to your server, only when you open or talk to Buddy, " +
                        "and only the latest one is kept. Choose \"approximate\" for neighbourhood-level only.",
                    color = Muted, fontSize = 13.sp,
                )
            }
            Switch(checked = shareLocation, onCheckedChange = { on ->
                if (on) askLocation.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION,
                                                   Manifest.permission.ACCESS_COARSE_LOCATION))
                else { shareLocation = false; prefs.shareLocation = false; LocationShare.stopSharing() }
            })
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Share calendar", color = Color.White)
                Text(
                    "This phone's calendars, including a work calendar that Outlook or Teams syncs here.",
                    color = Muted, fontSize = 13.sp,
                )
            }
            Switch(checked = shareCalendar, onCheckedChange = { on ->
                if (on) askCalendar.launch(Manifest.permission.READ_CALENDAR)
                else { shareCalendar = false; prefs.shareCalendar = false }
            })
        }
        if (shareCalendar) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Work calendars: busy times only", color = Color.White)
                    Text(
                        "Buddy sees \"Busy 10:00–11:00\" but no meeting titles or places from Exchange/Outlook " +
                            "calendars, so work details stay off your server.",
                        color = Muted, fontSize = 13.sp,
                    )
                }
                Switch(checked = workBusyOnly, onCheckedChange = {
                    workBusyOnly = it
                    prefs.workBusyOnly = it
                    BuddyService.instance?.syncCalendar()
                })
            }
        }

        if (!batteryOk()) {
            Column {
                Text("Keep Buddy awake", color = Color.White)
                Text(
                    "Samsung phones put background apps to sleep, which would stop reminders and Listen-in.",
                    color = Muted, fontSize = 13.sp,
                )
                OutlinedButton(onClick = onBattery, modifier = Modifier.padding(top = 4.dp)) {
                    Text("Allow Buddy to run in the background")
                }
            }
        }
    }
}


/** Pick Buddy's voice: tap one to hear it. The list is this phone's English voices. */
private val TALK_LANGUAGES = listOf(
    "" to "The phone's language",
    "auto:ml-IN,en-IN" to "Malayalam + English (switches between them)",
    "ml-IN" to "Malayalam only",
    "auto:hi-IN,en-IN" to "Hindi + English",
    "auto:ta-IN,en-IN" to "Tamil + English",
    "auto" to "Any language (Buddy works it out)",
)

/** Voice speed, and how long a pause means you've finished (fast talkers: short). */
@Composable
private fun SpeechTuning(prefs: BuddyPrefs) {
    var rate by remember { mutableStateOf(prefs.voiceRate) }
    var pauses by remember { mutableStateOf(prefs.pauses) }
    var talkLanguage by remember { mutableStateOf(prefs.talkLanguage) }
    var muteBeep by remember { mutableStateOf(prefs.muteListenBeep) }
    Column(Modifier.fillMaxWidth().padding(top = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 8.dp)) {
            Column(Modifier.weight(1f)) {
                Text("Silence the listening beep", color = Color.White)
                Text("The phone's speech recognition beeps each time it starts listening again. While Buddy " +
                    "listens quietly, media and system sounds stay muted; they come back when Buddy talks or " +
                    "another app plays audio. The Server speech engine never beeps.", color = Muted, fontSize = 12.sp)
            }
            Switch(checked = muteBeep, onCheckedChange = { muteBeep = it; prefs.muteListenBeep = it })
        }
        Text("My languages (Talk mode)", color = Color.White)
        Text("Buddy listens for these and replies in the one you use.", color = Muted, fontSize = 12.sp)
        Column(Modifier.padding(vertical = 4.dp)) {
            TALK_LANGUAGES.forEach { (value, label) ->
                Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().clickable {
                        talkLanguage = value
                        prefs.talkLanguage = value
                        prefs.simpleRecognition = false  // a new choice gets a fresh try
                        BuddyService.instance?.let { if (BuddyState.ui.value.listening) it.startListening(BuddyState.ui.value.mode) }
                    }.padding(vertical = 2.dp)) {
                    RadioButton(selected = talkLanguage == value, onClick = null)
                    Text(label, color = Color.White, modifier = Modifier.padding(start = 8.dp))
                }
            }
        }
        Text("Buddy's voice speed: ${String.format(java.util.Locale.US, "%.1f", rate)}×", color = Color.White)
        Slider(value = rate, onValueChange = { rate = it }, valueRange = 0.7f..1.4f, steps = 6,
            onValueChangeFinished = {
                prefs.voiceRate = rate
                BuddyService.instance?.previewVoice()
            })
        Text("Pauses when I talk", color = Color.White, modifier = Modifier.padding(top = 8.dp))
        Text("Short if you talk fast and Buddy waits too long; long if it cuts you off mid-thought.",
            color = Muted, fontSize = 12.sp)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 4.dp)) {
            listOf("short" to "Short", "normal" to "Normal", "long" to "Long").forEach { (value, label) ->
                FilterChip(selected = pauses == value, onClick = { pauses = value; prefs.pauses = value },
                    label = { Text(label) })
            }
        }
    }
}

@Composable
private fun VoicePicker(prefs: BuddyPrefs) {
    var chosen by remember { mutableStateOf(prefs.voiceName) }
    var open by remember { mutableStateOf(false) }
    val voices = remember(open) { BuddyService.instance?.voices().orEmpty() }
    Text("Buddy's voice", color = Color.White, fontSize = 18.sp)
    Text(
        "Tap a voice to hear it. Voices come from the phone's speech engine; for more, open Android " +
            "Settings → Text-to-speech output → the engine's settings → Install voice data.",
        color = Muted, fontSize = 13.sp,
    )
    OutlinedButton(onClick = { open = !open }) { Text(if (open) "Hide voices" else "Choose a voice") }
    if (!open) return
    if (voices.isEmpty()) {
        Text("Start Buddy first, then come back here to pick a voice.", color = Muted, fontSize = 13.sp)
        return
    }
    (listOf(VoiceChoice("", "Phone's default voice")) + voices).forEach { voice ->
        Row(
            Modifier.fillMaxWidth().clickable {
                chosen = voice.name
                BuddyService.instance?.useVoice(voice.name)
            }.padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            RadioButton(selected = chosen == voice.name, onClick = null)
            Spacer(Modifier.width(8.dp))
            Text(voice.label, color = Color.White, fontSize = 15.sp)
        }
    }
}
