package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.material3.OutlinedTextField
import androidx.compose.ui.platform.LocalContext
import androidx.health.connect.client.PermissionController
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** One outside service, as Buddy Connect (backend/buddy/gateway.py) reports it. */
data class Service(
    val name: String,
    val group: String,
    val title: String,
    val usedFor: String,
    val personal: Boolean,
    val setUp: Boolean,
    val lastOk: Boolean?,
    val lastAt: String,
    val lastError: String,
    val calls: Int,
    val failed: Int,
    val cost: Double,
    val canTest: Boolean,
    val setup: String,
    val missingKeys: List<String> = emptyList(),
    val canLink: Boolean = false,   // each user signs in to their own account (Connect), e.g. the Oura Ring
    val linked: Boolean = false,
) {
    /** gateway.state_of: failing, missing_keys (half set up), working, unused (set up, never called), not_set_up */
    val state: String get() = when {
        missingKeys.isNotEmpty() -> "missing_keys"
        !setUp -> "not_set_up"
        lastOk == false -> "failing"
        lastOk == true -> "working"
        else -> "unused"
    }

    /** what needs the owner first, then what works, then set up but unused, then not set up (gateway.STATES) */
    val rank: Int get() = ConnectStates.indexOf(state)

    val needsAttention: Boolean get() = state == "failing" || state == "missing_keys"
}

/** One block of the screen, as the server's gateway.arrange() sends it: kind is attention, group or not_set_up. */
data class ConnectSection(val title: String, val kind: String, val names: List<String>)

/** What /admin/connections answers: every service, and the sections to show them in. */
data class ConnectList(val services: List<Service>, val sections: List<ConnectSection>)

val ConnectStates = listOf("failing", "missing_keys", "working", "unused", "not_set_up")

/** The same order as gateway.arrange(), for servers that don't send sections yet. */
fun arrangeServices(services: List<Service>): List<ConnectSection> {
    val sorted = services.sortedWith(compareBy<Service>({ it.rank }, { groupOrder(it.group) }, { it.title.lowercase() }))
    val out = mutableListOf<ConnectSection>()
    sorted.filter { it.needsAttention }.takeIf { it.isNotEmpty() }?.let { list ->
        out += ConnectSection("Needs attention", "attention", list.map { it.name })
    }
    sorted.filter { it.state == "working" || it.state == "unused" }.groupBy { it.group }.toList()
        .sortedWith(compareBy({ groupOrder(it.first) }, { it.first }))
        .forEach { (g, list) -> out += ConnectSection(g, "group", list.map { it.name }) }
    sorted.filter { it.state == "not_set_up" }.takeIf { it.isNotEmpty() }?.let { list ->
        out += ConnectSection("Not set up", "not_set_up", list.map { it.name })
    }
    return out
}

/** "just now", "5 min ago", "3 h ago", "2 d ago" from the server's ISO time ("" when there's none). */
fun sinceText(iso: String): String {
    if (iso.isBlank()) return ""
    val at = runCatching { java.time.OffsetDateTime.parse(iso).toInstant() }
        .recoverCatching { java.time.LocalDateTime.parse(iso).toInstant(java.time.ZoneOffset.UTC) }
        .getOrNull() ?: return iso
    val minutes = java.time.Duration.between(at, java.time.Instant.now()).toMinutes()
    return when {
        minutes < 1 -> "just now"
        minutes < 60 -> "$minutes min ago"
        minutes < 48 * 60 -> "${minutes / 60} h ago"
        else -> "${minutes / (24 * 60)} d ago"
    }
}

/** GET /admin/connections and POST /admin/connections/test on your server. */
object ConnectionsApi {
    private val http = OkHttpClient.Builder().callTimeout(40, TimeUnit.SECONDS).build()

    suspend fun list(link: ServerLink): ConnectList = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + "/admin/connections")
            .header("Authorization", "Bearer ${link.token}")
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                error(if (response.code == 404) "update the server (sudo bash /opt/buddy/deploy/update.sh)"
                      else if (response.code == 401) "wrong token" else "server said ${response.code}")
            }
            val body = JSONObject(response.body?.string() ?: "{}")
            val array = body.optJSONArray("services")
            val services = (0 until (array?.length() ?: 0)).map { i ->
                val o = array!!.getJSONObject(i)
                Service(
                    name = o.optString("name"),
                    group = o.optString("group").ifEmpty { "Other" },
                    title = o.optString("title"),
                    usedFor = o.optString("used_for"),
                    personal = o.optString("privacy") == "personal",
                    setUp = o.optBoolean("set_up"),
                    lastOk = if (o.isNull("last_ok")) null else o.optBoolean("last_ok"),
                    lastAt = o.optString("last_at"),
                    lastError = o.optString("last_error"),
                    calls = o.optInt("calls_this_month"),
                    failed = o.optInt("failed_this_month"),
                    cost = o.optDouble("cost_this_month", 0.0).let { if (it.isNaN()) 0.0 else it },
                    canTest = o.optBoolean("can_test"),
                    setup = o.optString("setup"),
                    missingKeys = o.optJSONArray("missing_keys")?.let { a -> (0 until a.length()).map { a.optString(it) } }
                        ?: emptyList(),
                    canLink = o.optBoolean("can_link"),
                    linked = o.optBoolean("linked"),
                )
            }.sortedWith(compareBy<Service>({ groupOrder(it.group) }, { it.rank }, { it.title }))
            val sent = body.optJSONArray("sections")
            val known = services.map { it.name }.toSet()
            val sections = if (sent == null) arrangeServices(services) else (0 until sent.length()).map { i ->
                val o = sent.getJSONObject(i)
                val names = o.optJSONArray("names")
                ConnectSection(o.optString("title"), o.optString("kind"),
                    (0 until (names?.length() ?: 0)).map { names!!.optString(it) }.filter { it in known })
            }.filter { it.names.isNotEmpty() }
            // a service the sections left out still shows (under its own area), so nothing goes missing
            val placed = sections.flatMap { it.names }.toSet()
            val extra = services.filter { it.name !in placed }
            ConnectList(services, if (extra.isEmpty()) sections else sections + arrangeServices(extra))
        }
    }

    /** The sign-in link for a service each user connects to their own account (POST /admin/connections/link). */
    suspend fun signInLink(link: ServerLink, name: String): String = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + "/admin/connections/link")
            .header("Authorization", "Bearer ${link.token}")
            .post(JSONObject().put("name", name).toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { response ->
            val o = runCatching { JSONObject(response.body?.string() ?: "{}") }.getOrDefault(JSONObject())
            if (!response.isSuccessful) error(o.optString("detail").ifEmpty { "server said ${response.code}" })
            o.optString("url").ifEmpty { error("no link came back") }
        }
    }

    suspend fun test(link: ServerLink, name: String): Pair<Boolean, String> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + "/admin/connections/test")
            .header("Authorization", "Bearer ${link.token}")
            .post(JSONObject().put("name", name).toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(request).execute().use { response ->
            val o = runCatching { JSONObject(response.body?.string() ?: "{}") }.getOrDefault(JSONObject())
            if (!response.isSuccessful) Pair(false, "server said ${response.code}")
            else Pair(o.optBoolean("ok"), o.optString("said"))
        }
    }
}

/** The areas services are grouped in, in the order the screen shows them (gateway.GROUPS). */
val ConnectGroups = listOf("Travel & transit", "Finance & markets", "Shopping", "Health", "Home", "Calls", "Search", "News & trends", "Weather", "AI models")

fun groupOrder(group: String): Int = ConnectGroups.indexOf(group).let { if (it < 0) ConnectGroups.size else it }

/** Short label and colour for a service's state. */
private fun stateOf(s: Service): Pair<String, Color> = when (s.state) {
    "missing_keys" -> "Missing keys" to Pal.bad
    "not_set_up" -> "Not set up" to Pal.dim
    "failing" -> "Failing" to Pal.bad
    "working" -> "Working" to Pal.accent
    else -> "Ready" to Pal.warn
}

/** The one line under a service's name when its row is folded: what you'd want to know at a glance. */
private fun glanceOf(s: Service): String = when (s.state) {
    "failing" -> s.lastError.ifEmpty { "Last call failed" }
    "missing_keys" -> "Missing " + s.missingKeys.joinToString(", ")
    "not_set_up" -> if (s.canLink) "Tap to connect your account" else s.usedFor
    "unused" -> "Set up · not used yet"
    else -> listOfNotNull(
        sinceText(s.lastAt).takeIf { it.isNotEmpty() }?.let { "synced $it" },
        if (s.cost > 0) "$" + "%.2f".format(s.cost) + " this month" else null,
    ).joinToString(" · ").ifEmpty { s.usedFor }
}

/** The status filters at the top, each with its count. */
private enum class Show(val label: String) { All("All"), Attention("Needs attention"), Working("Working"), Idle("Not set up") }

private fun Show.keeps(s: Service): Boolean = when (this) {
    Show.All -> true
    Show.Attention -> s.needsAttention
    Show.Working -> s.state == "working" || s.state == "unused"
    Show.Idle -> s.state == "not_set_up"
}

/** Buddy Connect: every outside service Buddy uses, grouped, with status filters; tap a service for its details. */
@Composable
fun ConnectionsScreen(link: ServerLink?) {
    var services by remember { mutableStateOf<List<Service>>(emptyList()) }
    var sections by remember { mutableStateOf<List<ConnectSection>>(emptyList()) }
    var showIdle by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }
    var reload by remember { mutableStateOf(0) }
    val tests = remember { mutableStateMapOf<String, String>() }
    val open = remember { mutableStateMapOf<String, Boolean>() }
    var query by remember { mutableStateOf("") }
    var group by remember { mutableStateOf("All") }
    var show by remember { mutableStateOf(Show.All) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var healthNote by remember { mutableStateOf("") }
    fun syncHealth() {
        if (link == null) return
        healthNote = "Syncing…"
        scope.launch {
            healthNote = runCatching { "✓ Sent ${HealthSync.sync(context, link)} days to your server" }
                .getOrElse { "✗ ${it.message}" }
            reload++
        }
    }
    val askHealth = rememberLauncherForActivityResult(PermissionController.createRequestPermissionResultContract()) {
        granted -> if (granted.isNotEmpty()) syncHealth() else healthNote = "Nothing allowed, so nothing was read."
    }
    var contactsNote by remember { mutableStateOf("") }
    fun syncContacts() {
        if (link == null) return
        contactsNote = "Syncing…"
        scope.launch {
            contactsNote = runCatching { "✓ Sent ${ContactsSync.sync(context, link)} contacts to your server" }
                .getOrElse { "✗ ${it.message}" }
            reload++
        }
    }
    val askContacts = rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) { ContactsSync.setEnabled(context, true); syncContacts() } else contactsNote = "Not allowed, so nothing was read."
    }

    // Back from a sign-in page (Connect): show the new state.
    val lifecycle = androidx.compose.ui.platform.LocalLifecycleOwner.current.lifecycle
    androidx.compose.runtime.DisposableEffect(lifecycle) {
        val watcher = androidx.lifecycle.LifecycleEventObserver { _, event ->
            if (event == androidx.lifecycle.Lifecycle.Event.ON_RESUME && tests.values.any { it == "Opening sign-in…" }) {
                tests.keys.filter { tests[it] == "Opening sign-in…" }.forEach { tests.remove(it) }
                reload++
            }
        }
        lifecycle.addObserver(watcher)
        onDispose { lifecycle.removeObserver(watcher) }
    }

    LaunchedEffect(link, reload) {
        if (link == null) {
            problem = "Connect to your server in Settings first."
            loading = false
            return@LaunchedEffect
        }
        loading = true
        try {
            val got = ConnectionsApi.list(link)
            services = got.services
            sections = got.sections
            problem = ""
        } catch (e: Exception) {
            problem = "Couldn't load connections: ${e.message}"
        }
        loading = false
    }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        // header: title, one quiet summary line, refresh
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Connections", color = Pal.text, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                if (services.isNotEmpty()) {
                    val cost = services.sumOf { it.cost }
                    val active = services.count { it.setUp }
                    Text("$active of ${services.size} services set up" +
                        (if (cost > 0) " · $" + "%.2f".format(cost) + " this month" else ""),
                        color = Pal.dim, fontSize = 13.sp)
                }
            }
            TextButton(onClick = { reload++ }, enabled = !loading) {
                Text(if (loading) "Loading…" else "↻ Refresh", color = Pal.link)
            }
        }
        if (problem.isNotEmpty()) {
            Text(problem, color = Pal.bad, fontSize = 14.sp, modifier = Modifier.padding(vertical = 8.dp))
        }

        // status filters with counts: the first thing to scan
        if (services.isNotEmpty()) {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = 10.dp, bottom = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Show.entries.forEach { f ->
                    val n = services.count { f.keeps(it) }
                    if (f == Show.Attention && n == 0) return@forEach
                    val on = f == show
                    val tint = if (f == Show.Attention) Pal.bad else Pal.accent
                    Row(
                        Modifier.background(if (on) tint else Pal.panel, RoundedCornerShape(18.dp))
                            .clickable { show = if (on) Show.All else f }
                            .padding(horizontal = 12.dp, vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        if (f == Show.Attention && !on) {
                            Spacer(Modifier.size(7.dp).background(Pal.bad, CircleShape))
                            Spacer(Modifier.size(6.dp))
                        }
                        Text(f.label, color = if (on) Pal.bg else Pal.bright, fontSize = 13.sp,
                            fontWeight = FontWeight.Medium)
                        Spacer(Modifier.size(6.dp))
                        Text("$n", color = if (on) Pal.bg else Pal.dim, fontSize = 13.sp)
                    }
                }
            }
        }

        OutlinedTextField(
            value = query, onValueChange = { query = it }, singleLine = true,
            placeholder = { Text("Search services", color = Pal.dim) },
            trailingIcon = { if (query.isNotEmpty()) TextButton(onClick = { query = "" }) { Text("✕", color = Pal.dim) } },
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
        )
        val groupsPresent = ConnectGroups.filter { g -> services.any { it.group == g } }
        if (groupsPresent.size > 1) {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                (listOf("All") + groupsPresent).forEach { g ->
                    val on = g == group
                    Text(if (g == "All") "All areas" else g, color = if (on) Pal.text else Pal.dim, fontSize = 12.sp,
                        fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                        modifier = Modifier.background(if (on) Pal.track else Color.Transparent, RoundedCornerShape(12.dp))
                            .clickable { group = g }.padding(horizontal = 10.dp, vertical = 5.dp))
                }
            }
        }

        val words = query.lowercase().split(" ").filter { it.length > 1 }
        val shown = services.filter { s ->
            show.keeps(s) && (group == "All" || s.group == group) && (words.isEmpty() || run {
                val hay = (s.title + " " + s.usedFor + " " + s.group + " " + s.name.replace("_", " ") + " " +
                    stateOf(s).first).lowercase()
                words.all { hay.contains(it) }
            })
        }
        if (!loading && services.isNotEmpty() && shown.isEmpty()) {
            Text(if (query.isNotBlank()) "Nothing matches that. Ask Buddy: it can tell you what would need adding."
                 else "Nothing here.",
                color = Pal.dim, fontSize = 14.sp, modifier = Modifier.padding(vertical = 12.dp))
        }
        val byName = shown.associateBy { it.name }
        val visible = sections.map { sec -> sec to sec.names.mapNotNull { byName[it] } }.filter { it.second.isNotEmpty() }
        // Not set up stays folded unless you're filtering, searching or picked an area: what's in use comes first.
        val idleOpen = showIdle || query.isNotBlank() || group != "All" || show == Show.Idle

        LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            visible.forEach { (sec, members) ->
                val folded = sec.kind == "not_set_up" && !idleOpen
                item(key = "section:${sec.kind}:${sec.title}") {
                    SectionHeader(sec, members.size, folded, onToggle = { showIdle = !showIdle })
                }
                if (!folded) item(key = "card:${sec.kind}:${sec.title}") {
                    Column(Modifier.fillMaxWidth().background(Pal.panel, RoundedCornerShape(14.dp))) {
                        members.forEachIndexed { i, s ->
                            if (i > 0) Spacer(Modifier.fillMaxWidth().padding(start = 34.dp).height(1.dp)
                                .background(Pal.track))
                            ServiceRow(
                                s = s, showGroup = sec.kind != "group", link = link,
                                expanded = open[s.name] == true, onToggle = { open[s.name] = open[s.name] != true },
                                testSaid = tests[s.name],
                                healthNote = healthNote,
                                onHealthPermissions = { askHealth.launch(HealthSync.PERMISSIONS) },
                                onHealthSync = { syncHealth() },
                                contactsNote = contactsNote,
                                onContactsAllow = { askContacts.launch(android.Manifest.permission.READ_CONTACTS) },
                                onContactsShare = { ContactsSync.setEnabled(context, true); syncContacts() },
                                onContactsSync = { syncContacts() },
                                onConnect = {
                                    if (link != null) {
                                        tests[s.name] = "Getting the sign-in link…"
                                        scope.launch {
                                            runCatching { ConnectionsApi.signInLink(link, s.name) }
                                                .onSuccess { url ->
                                                    tests[s.name] = "Opening sign-in…"
                                                    context.startActivity(android.content.Intent(
                                                        android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url)))
                                                }
                                                .onFailure { tests[s.name] = "✗ ${it.message}" }
                                        }
                                    }
                                },
                                onTest = {
                                    if (link != null) {
                                        tests[s.name] = "Testing…"
                                        scope.launch {
                                            val (ok, said) = runCatching { ConnectionsApi.test(link, s.name) }
                                                .getOrElse { Pair(false, it.message ?: "couldn't reach the server") }
                                            tests[s.name] = (if (ok) "✓ " else "✗ ") + said
                                            reload++
                                        }
                                    }
                                },
                            )
                        }
                    }
                }
            }
            item { Spacer(Modifier.size(24.dp)) }
        }
    }
}

@Composable
private fun SectionHeader(sec: ConnectSection, count: Int, folded: Boolean, onToggle: () -> Unit) {
    val attention = sec.kind == "attention"
    Row(
        Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 2.dp, start = 4.dp, end = 4.dp)
            .then(if (sec.kind == "not_set_up") Modifier.clickable { onToggle() } else Modifier),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(sec.title.uppercase(), color = if (attention) Pal.bad else Pal.dim, fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold, letterSpacing = 0.8.sp, modifier = Modifier.weight(1f))
        Text(
            "$count" + if (sec.kind == "not_set_up") (if (folded) "  ·  Show ▸" else "  ·  Hide ▾") else "",
            color = if (sec.kind == "not_set_up") Pal.link else Pal.dim, fontSize = 12.sp,
        )
    }
}

/** One service: a single scannable line (dot, name, what matters now, state); tap for details and actions. */
@Composable
private fun ServiceRow(
    s: Service,
    showGroup: Boolean,
    link: ServerLink?,
    expanded: Boolean,
    onToggle: () -> Unit,
    testSaid: String?,
    healthNote: String,
    onHealthPermissions: () -> Unit,
    onHealthSync: () -> Unit,
    contactsNote: String,
    onContactsAllow: () -> Unit,
    onContactsShare: () -> Unit,
    onContactsSync: () -> Unit,
    onConnect: () -> Unit,
    onTest: () -> Unit,
) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val (label, color) = stateOf(s)
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().clickable { onToggle() }.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Spacer(Modifier.size(9.dp).background(color, CircleShape))
            Spacer(Modifier.size(11.dp))
            Column(Modifier.weight(1f)) {
                Text(s.title, color = Pal.text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                val glance = glanceOf(s)
                if (glance.isNotEmpty() && !expanded) {
                    Text(glance, color = if (s.needsAttention) Pal.bad else Pal.dim, fontSize = 12.sp,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            Spacer(Modifier.size(8.dp))
            if (s.state != "working") Text(label, color = color, fontSize = 12.sp)
            Text(if (expanded) "  ▾" else "  ›", color = Pal.dim, fontSize = 16.sp)
        }
        if (!expanded) return@Column

        Column(Modifier.fillMaxWidth().padding(start = 34.dp, end = 14.dp, bottom = 12.dp)) {
            if (s.usedFor.isNotEmpty()) Text(s.usedFor, color = Pal.soft, fontSize = 13.sp)
            val since = sinceText(s.lastAt)
            val facts = listOfNotNull(
                if (showGroup) s.group else null,
                label,
                if (s.setUp) (if (since.isEmpty()) "never used" else "last sync $since") else null,
                if (s.calls > 0) "${s.calls} calls" + (if (s.failed > 0) " (${s.failed} failed)" else "") + " this month" else null,
                if (s.cost > 0) "$" + "%.2f".format(s.cost) + " this month" else null,
            )
            Text(facts.joinToString(" · "), color = Pal.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
            if (s.personal) {
                Text("🔒 May see your personal data", color = Pal.warn, fontSize = 12.sp,
                    modifier = Modifier.padding(top = 4.dp))
            }
            if (s.setUp && s.lastOk == false && s.lastError.isNotEmpty()) {
                Text(s.lastError, color = Pal.bad, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
            }
            if (s.missingKeys.isNotEmpty()) {
                Text("Missing: " + s.missingKeys.joinToString(", "), color = Pal.bad, fontSize = 13.sp,
                    modifier = Modifier.padding(top = 8.dp))
            }
            if ((!s.setUp || s.missingKeys.isNotEmpty()) && s.setup.isNotEmpty()) {
                val commands = s.setup.replace("; ", "\n") + "\nsudo systemctl restart buddy"
                Row(Modifier.fillMaxWidth().padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("To set it up, on the server:", color = Pal.dim, fontSize = 12.sp, modifier = Modifier.weight(1f))
                    TextButton(onClick = { clipboard.setText(AnnotatedString(commands)) }) {
                        Text("Copy", color = Pal.link, fontSize = 13.sp)
                    }
                }
                SelectionContainer {
                    Text(commands, color = Pal.bright, fontSize = 12.sp, fontFamily = FontFamily.Monospace,
                        modifier = Modifier.fillMaxWidth().background(Pal.bg, RoundedCornerShape(8.dp)).padding(10.dp))
                }
            }
            if (s.name == "phone_contacts" && link != null) {  // names and numbers only, read on the phone
                Text("Names and phone numbers only, with your permission; sent only to your Buddy server.",
                    color = Pal.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
                if (contactsNote.isNotEmpty()) Text(contactsNote, color = Pal.soft, fontSize = 13.sp)
            }
            if (s.name == "health_connect") {  // the phone's own data: permission here, read on the phone
                Text(
                    if (!HealthSync.available(context)) "Health Connect isn't on this phone (install it from Play)."
                    else "Read only, with your permission; sent only to your Buddy server.",
                    color = Pal.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
                if (healthNote.isNotEmpty()) Text(healthNote, color = Pal.soft, fontSize = 13.sp)
            }
            testSaid?.let { said ->
                Text(said, color = when {
                    said.startsWith("✓") -> Pal.accent
                    said.startsWith("✗") -> Pal.bad
                    else -> Pal.dim
                }, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
            }
            // actions, together on one line at the bottom
            Row(Modifier.padding(top = 4.dp)) {
                if (s.canLink && link != null) {
                    TextButton(onClick = onConnect) { Text(if (s.linked) "Reconnect" else "Connect", color = Pal.link) }
                }
                if (s.name == "phone_contacts" && link != null) {
                    when {
                        !ContactsSync.allowed(context) ->
                            TextButton(onClick = onContactsAllow) { Text("Allow", color = Pal.link) }
                        !ContactsSync.enabled(context) ->
                            TextButton(onClick = onContactsShare) { Text("Share contacts", color = Pal.link) }
                        else -> TextButton(onClick = onContactsSync) { Text("Sync now", color = Pal.link) }
                    }
                }
                if (s.name == "health_connect" && HealthSync.available(context) && link != null) {
                    TextButton(onClick = onHealthPermissions) {
                        Text(if (s.lastOk == true) "Permissions" else "Connect", color = Pal.link)
                    }
                    TextButton(onClick = onHealthSync) { Text("Sync now", color = Pal.link) }
                }
                if (s.canTest && s.setUp && link != null) {
                    TextButton(onClick = onTest) { Text("Test", color = Pal.link) }
                }
            }
        }
    }
}
