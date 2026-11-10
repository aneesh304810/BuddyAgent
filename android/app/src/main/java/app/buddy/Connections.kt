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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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

private fun stateOf(s: Service): Pair<String, Color> = when (s.state) {
    "missing_keys" -> "Missing keys" to Pal.bad
    "not_set_up" -> "Not set up" to Pal.dim
    "failing" -> "Failing" to Pal.bad
    "working" -> "Working" to Pal.accent
    else -> "Set up · not used yet" to Pal.warn
}

/** Buddy Connect: every outside service Buddy uses, whether it works, and a Test button for each. */
@Composable
fun ConnectionsScreen(link: ServerLink?) {
    var services by remember { mutableStateOf<List<Service>>(emptyList()) }
    var sections by remember { mutableStateOf<List<ConnectSection>>(emptyList()) }
    var showIdle by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }
    var reload by remember { mutableStateOf(0) }
    val tests = remember { mutableStateMapOf<String, String>() }
    var query by remember { mutableStateOf("") }
    var group by remember { mutableStateOf("All") }
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
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("Connections", color = Pal.text, fontSize = 22.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.weight(1f))
            TextButton(onClick = { reload++ }) { Text(if (loading) "Loading…" else "Refresh", color = Pal.link) }
        }
        if (problem.isNotEmpty()) {
            Text(problem, color = Pal.bad, fontSize = 14.sp, modifier = Modifier.padding(vertical = 8.dp))
        }
        if (services.isNotEmpty()) {
            val attention = services.count { it.needsAttention }
            val working = services.count { it.state == "working" }
            val cost = services.sumOf { it.cost }
            Text(
                (if (attention > 0) "$attention need attention · " else "") +
                    "$working working · ${services.count { it.state == "not_set_up" }} not set up" +
                    (if (cost > 0) " · $" + "%.2f".format(cost) + " this month" else ""),
                color = Pal.soft, fontSize = 14.sp, modifier = Modifier.padding(bottom = 8.dp),
            )
        }
        OutlinedTextField(
            value = query, onValueChange = { query = it }, singleLine = true,
            placeholder = { Text("Search: \"train\", \"stocks\", \"weather\", \"not set up\"…") },
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
        )
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            (listOf("All") + ConnectGroups.filter { g -> services.any { it.group == g } }).forEach { g ->
                val on = g == group
                Text(g, color = if (on) Pal.bg else Pal.soft, fontSize = 13.sp,
                    modifier = Modifier.background(if (on) Pal.accent else Pal.track, RoundedCornerShape(16.dp))
                        .clickable { group = g }.padding(horizontal = 12.dp, vertical = 6.dp))
            }
        }
        val words = query.lowercase().split(" ").filter { it.length > 1 }
        val inGroup = if (group == "All") services else services.filter { it.group == group }
        val shown = if (words.isEmpty()) inGroup else inGroup.filter { s ->
            val hay = (s.title + " " + s.usedFor + " " + s.group + " " + s.name.replace("_", " ") + " " +
                stateOf(s).first).lowercase()
            words.all { hay.contains(it) }
        }
        if (query.isNotBlank() && shown.isEmpty()) {
            Text("No service in Buddy Connect matches that. Ask Buddy: it can tell you what would need adding.",
                color = Pal.dim, fontSize = 14.sp, modifier = Modifier.padding(vertical = 8.dp))
        }
        val byName = shown.associateBy { it.name }
        val visible = sections.map { sec -> sec to sec.names.mapNotNull { byName[it] } }.filter { it.second.isNotEmpty() }
        // Not set up is folded away unless you're searching or picked an area: what's in use comes first.
        val idleOpen = showIdle || query.isNotBlank() || group != "All"
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            visible.forEach { (sec, members) ->
              val folded = sec.kind == "not_set_up" && !idleOpen
              item(key = "section:${sec.kind}:${sec.title}") {
                Row(
                    Modifier.fillMaxWidth().padding(top = 6.dp)
                        .then(if (sec.kind == "not_set_up") Modifier.clickable { showIdle = !showIdle } else Modifier),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        (if (sec.kind == "attention") "⚠ " else "") + sec.title,
                        color = if (sec.kind == "attention") Pal.bad else Pal.bright,
                        fontSize = 15.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f),
                    )
                    Text(
                        "${members.size}" + if (sec.kind == "not_set_up") (if (folded) "  ▸ show" else "  ▾ hide") else "",
                        color = Pal.dim, fontSize = 13.sp,
                    )
                }
              }
              if (!folded) items(members, key = { it.name }) { s ->
                ServiceRow(
                    s = s, showGroup = sec.kind != "group", link = link, testSaid = tests[s.name],
                    healthNote = healthNote,
                    onHealthPermissions = { askHealth.launch(HealthSync.PERMISSIONS) },
                    onHealthSync = { syncHealth() },
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
            item { Spacer(Modifier.size(24.dp)) }
        }
    }
}

/** One service: name, state and last sync on top, so the list scans quickly; details and actions below. */
@Composable
private fun ServiceRow(
    s: Service,
    showGroup: Boolean,
    link: ServerLink?,
    testSaid: String?,
    healthNote: String,
    onHealthPermissions: () -> Unit,
    onHealthSync: () -> Unit,
    onTest: () -> Unit,
) {
    val context = LocalContext.current
    val (label, color) = stateOf(s)
    Column(
        Modifier.fillMaxWidth()
            .background(Pal.panel, RoundedCornerShape(14.dp))
            .padding(14.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Spacer(Modifier.size(10.dp).background(color, CircleShape))
            Spacer(Modifier.size(8.dp))
            Text(s.title, color = Pal.text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f))
            Text(label, color = color, fontSize = 13.sp)
        }
        val since = sinceText(s.lastAt)
        val facts = listOfNotNull(
            if (showGroup) s.group else null,
            if (s.setUp) (if (since.isEmpty()) "never synced" else "last sync $since") else null,
            if (s.calls > 0) "${s.calls} calls this month" + (if (s.failed > 0) " · ${s.failed} failed" else "") else null,
            if (s.cost > 0) "$" + "%.2f".format(s.cost) else null,
        )
        if (facts.isNotEmpty()) {
            Text(facts.joinToString(" · "), color = Pal.soft, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp))
        }
        if (s.usedFor.isNotEmpty()) {
            Text(s.usedFor + if (s.personal) " · may see your personal data" else "",
                color = Pal.dim, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp))
        }
        if (s.setUp && s.lastOk == false && s.lastError.isNotEmpty()) {
            Text(s.lastError, color = Pal.bad, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
        }
        if (s.missingKeys.isNotEmpty()) {
            Text("Missing: " + s.missingKeys.joinToString(", "), color = Pal.bad, fontSize = 13.sp,
                modifier = Modifier.padding(top = 6.dp))
        }
        if ((!s.setUp || s.missingKeys.isNotEmpty()) && s.setup.isNotEmpty()) {
            Text("To attach it, on the server:", color = Pal.dim, fontSize = 12.sp,
                modifier = Modifier.padding(top = 6.dp))
            SelectionContainer {
                Text(s.setup.replace("; ", "\n") + "\nsudo systemctl restart buddy", color = Pal.bright, fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace)
            }
        }
        testSaid?.let { said ->
            Text(said, color = when {
                said.startsWith("✓") -> Pal.accent
                said.startsWith("✗") -> Pal.bad
                else -> Pal.dim
            }, fontSize = 13.sp,
                modifier = Modifier.padding(top = 6.dp))
        }
        if (s.name == "health_connect") {  // the phone's own data: permission here, read on the phone
            Text(
                if (!HealthSync.available(context)) "Health Connect isn't on this phone (install it from Play)."
                else "Read only, with your permission; sent only to your Buddy server.",
                color = Pal.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
            if (healthNote.isNotEmpty()) Text(healthNote, color = Pal.soft, fontSize = 13.sp)
            if (HealthSync.available(context) && link != null) {
                Row {
                    TextButton(onClick = onHealthPermissions) {
                        Text(if (s.lastOk == true) "Permissions" else "Connect", color = Pal.link)
                    }
                    TextButton(onClick = onHealthSync) { Text("Sync now", color = Pal.link) }
                }
            }
        }
        if (s.canTest && s.setUp && link != null) {
            TextButton(onClick = onTest) { Text("Test", color = Pal.link) }
        }
    }
}
