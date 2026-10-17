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
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Languages people around you might speak, for Listen-in. South America first. */
val ROOM_LANGUAGES = listOf(
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
private val Muted = Color(0xFF8A93B2)

class MainActivity : ComponentActivity() {
    private lateinit var prefs: BuddyPrefs

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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = BuddyPrefs(this)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Accent, background = Ink, surface = Panel)) {
                BuddyScreen(
                    prefs = prefs,
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
    onSaveLink: (ServerLink) -> Unit,
    onStart: () -> Unit,
    onBattery: () -> Unit,
    batteryOk: () -> Boolean,
) {
    val ui by BuddyState.ui.collectAsState()
    var showSettings by remember { mutableStateOf(prefs.link == null) }

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
            TextButton(onClick = { showSettings = !showSettings }) {
                Text(if (showSettings) "Done" else "Settings")
            }
        }

        if (showSettings) {
            SettingsPanel(prefs, onSaveLink, onBattery, batteryOk, onDone = { showSettings = false })
            return@Column
        }

        if (!ui.running) {
            Button(onClick = onStart, modifier = Modifier.fillMaxWidth()) { Text("Start Buddy") }
        }

        Transcript(ui.lines, Modifier.weight(1f))
        Controls(ui, prefs)
    }
}

@Composable
private fun Transcript(lines: List<Line>, modifier: Modifier) {
    val state = rememberLazyListState()
    LaunchedEffect(lines.size, lines.lastOrNull()?.text) {
        if (lines.isNotEmpty()) state.animateScrollToItem(lines.lastIndex)
    }
    LazyColumn(modifier.fillMaxWidth(), state = state, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        items(lines) { line ->
            val mine = line.from == Speaker.YOU
            Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
                Text(
                    line.text,
                    color = when (line.from) {
                        Speaker.ERROR -> Color(0xFFFF8A80)
                        Speaker.WHISPER -> Color(0xFFB9C6FF)
                        else -> Color.White
                    },
                    fontSize = if (line.from == Speaker.WHISPER) 14.sp else 16.sp,
                    modifier = Modifier
                        .widthIn(max = 300.dp)
                        .background(
                            when (line.from) {
                                Speaker.YOU -> Accent.copy(alpha = 0.35f)
                                Speaker.NUDGE -> Talking.copy(alpha = 0.25f)
                                else -> Panel
                            },
                            RoundedCornerShape(14.dp),
                        )
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun Controls(ui: UiState, prefs: BuddyPrefs) {
    val service = BuddyService.instance
    var handsFree by remember { mutableStateOf(prefs.handsFree) }
    var roomLanguage by remember { mutableStateOf(prefs.roomLanguage) }
    var languageMenu by remember { mutableStateOf(false) }
    var typed by remember { mutableStateOf("") }

    Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = ui.mode == Mode.TALK,
                onClick = { switchMode(Mode.TALK, ui) },
                label = { Text("Talk") },
            )
            FilterChip(
                selected = ui.mode == Mode.LISTEN_IN,
                onClick = { switchMode(Mode.LISTEN_IN, ui) },
                label = { Text("Listen-in") },
            )
        }

        val orbColor by animateColorAsState(
            when {
                ui.speaking -> Talking
                ui.listening -> Listening
                else -> Accent
            },
            label = "orb",
        )
        Box(
            Modifier
                .padding(vertical = 16.dp)
                .size(120.dp)
                .background(orbColor, CircleShape)
                .clickable(enabled = service != null) { service?.toggle() },
        )
        Text(
            when {
                ui.onCall -> "Paused while you're on a call"
                ui.speaking -> "Buddy is talking. Just speak to interrupt."
                ui.listening && ui.mode == Mode.LISTEN_IN -> "Listening to the room. Say \"Buddy, …\" to ask me."
                ui.listening -> "Listening…"
                ui.mode == Mode.LISTEN_IN -> "Tap to listen to the room"
                else -> "Tap the orb or your earbud to talk"
            },
            color = Muted,
            fontSize = 14.sp,
        )

        Spacer(Modifier.height(8.dp))
        if (ui.mode == Mode.TALK) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Hands-free", color = Color.White, modifier = Modifier.padding(end = 8.dp))
                Switch(checked = handsFree, onCheckedChange = { handsFree = it; prefs.handsFree = it })
            }
        } else {
            Box {
                OutlinedButton(onClick = { languageMenu = true }) {
                    Text("Room language: " + (ROOM_LANGUAGES.firstOrNull { it.first == roomLanguage }?.second ?: roomLanguage))
                }
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

        // Typing works too: on a quiet train, or when speech recognition struggles.
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = typed,
                onValueChange = { typed = it },
                placeholder = { Text("Type to Buddy") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            TextButton(
                enabled = typed.isNotBlank() && service != null,
                onClick = { service?.sayToBuddy(typed.trim()); typed = "" },
            ) { Text("Send") }
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

    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
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
                        "so Buddy's voice sounds more like a phone call. Listen-in always uses the phone's mic.",
                    color = Muted, fontSize = 13.sp,
                )
            }
            Switch(checked = earbudMic, onCheckedChange = { earbudMic = it; prefs.earbudMic = it })
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
