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
) {
    /** failing first, then working, then set up but unused, then not set up */
    val rank: Int get() = when {
        !setUp -> 3
        lastOk == false -> 0
        lastOk == true -> 1
        else -> 2
    }
}

/** GET /admin/connections and POST /admin/connections/test on your server. */
object ConnectionsApi {
    private val http = OkHttpClient.Builder().callTimeout(40, TimeUnit.SECONDS).build()

    suspend fun list(link: ServerLink): List<Service> = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + "/admin/connections")
            .header("Authorization", "Bearer ${link.token}")
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                error(if (response.code == 404) "update the server (sudo bash /opt/buddy/deploy/update.sh)"
                      else if (response.code == 401) "wrong token" else "server said ${response.code}")
            }
            val array = JSONObject(response.body?.string() ?: "{}").optJSONArray("services")
            (0 until (array?.length() ?: 0)).map { i ->
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
                )
            }.sortedWith(compareBy<Service>({ groupOrder(it.group) }, { it.rank }, { it.title }))
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

private fun stateOf(s: Service): Pair<String, Color> = when {
    !s.setUp -> "Not set up" to Pal.dim
    s.lastOk == false -> "Failing" to Pal.bad
    s.lastOk == true -> "Working" to Pal.accent
    else -> "Set up · not used yet" to Pal.warn
}

/** Buddy Connect: every outside service Buddy uses, whether it works, and a Test button for each. */
@Composable
fun ConnectionsScreen(link: ServerLink?) {
    var services by remember { mutableStateOf<List<Service>>(emptyList()) }
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

    LaunchedEffect(link, reload) {
        if (link == null) {
            problem = "Connect to your server in Settings first."
            loading = false
            return@LaunchedEffect
        }
        loading = true
        try {
            services = ConnectionsApi.list(link)
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
            val failing = services.count { it.setUp && it.lastOk == false }
            val working = services.count { it.setUp && it.lastOk == true }
            val cost = services.sumOf { it.cost }
            Text(
                "$working working · $failing failing · ${services.count { !it.setUp }} not set up" +
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
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            shown.groupBy { it.group }.forEach { (header, members) ->
              item(key = "group:$header") {
                Text(header, color = Pal.bright, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.padding(top = 6.dp))
              }
              items(members, key = { it.name }) { s ->
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
                    if (s.usedFor.isNotEmpty()) {
                        Text(s.usedFor + if (s.personal) " · may see your personal data" else "",
                            color = Pal.dim, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp))
                    }
                    if (s.setUp && s.lastOk == false && s.lastError.isNotEmpty()) {
                        Text(s.lastError, color = Pal.bad, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
                    }
                    if (s.calls > 0) {
                        Text(
                            "${s.calls} calls this month" + (if (s.failed > 0) " · ${s.failed} failed" else "") +
                                (if (s.cost > 0) " · $" + "%.2f".format(s.cost) else ""),
                            color = Pal.soft, fontSize = 13.sp, modifier = Modifier.padding(top = 4.dp),
                        )
                    }
                    if (!s.setUp && s.setup.isNotEmpty()) {
                        Text("To attach it, on the server:", color = Pal.dim, fontSize = 12.sp,
                            modifier = Modifier.padding(top = 6.dp))
                        SelectionContainer {
                            Text(s.setup.replace("; ", "\n") + "\nsudo systemctl restart buddy", color = Pal.bright, fontSize = 12.sp,
                                fontFamily = FontFamily.Monospace)
                        }
                    }
                    tests[s.name]?.let { said ->
                        Text(said, color = when {
                            said.startsWith("✓") -> Pal.accent
                            said.startsWith("✗") -> Pal.bad
                            else -> Pal.dim
                        }, fontSize = 13.sp,
                            modifier = Modifier.padding(top = 6.dp))
                    }
                    if (s.name == "phone_contacts" && link != null) {  // names and numbers only, read on the phone
                        Text("Names and phone numbers only, with your permission; sent only to your Buddy server.",
                            color = Pal.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                        if (contactsNote.isNotEmpty()) Text(contactsNote, color = Pal.soft, fontSize = 13.sp)
                        Row {
                            if (!ContactsSync.allowed(context)) {
                                TextButton(onClick = { askContacts.launch(android.Manifest.permission.READ_CONTACTS) }) {
                                    Text("Allow", color = Pal.link)
                                }
                            } else if (!ContactsSync.enabled(context)) {
                                TextButton(onClick = { ContactsSync.setEnabled(context, true); syncContacts() }) {
                                    Text("Share contacts", color = Pal.link)
                                }
                            } else {
                                TextButton(onClick = { syncContacts() }) { Text("Sync now", color = Pal.link) }
                            }
                        }
                    }
                    if (s.name == "health_connect") {  // the phone's own data: permission here, read on the phone
                        Text(
                            if (!HealthSync.available(context)) "Health Connect isn't on this phone (install it from Play)."
                            else "Read only, with your permission; sent only to your Buddy server.",
                            color = Pal.dim, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                        if (healthNote.isNotEmpty()) Text(healthNote, color = Pal.soft, fontSize = 13.sp)
                        if (HealthSync.available(context) && link != null) {
                            Row {
                                TextButton(onClick = { askHealth.launch(HealthSync.PERMISSIONS) }) {
                                    Text(if (s.lastOk == true) "Permissions" else "Connect", color = Pal.link)
                                }
                                TextButton(onClick = { syncHealth() }) { Text("Sync now", color = Pal.link) }
                            }
                        }
                    }
                    if (s.canTest && s.setUp && link != null) {
                        TextButton(onClick = {
                            tests[s.name] = "Testing…"
                            scope.launch {
                                val (ok, said) = runCatching { ConnectionsApi.test(link, s.name) }
                                    .getOrElse { Pair(false, it.message ?: "couldn't reach the server") }
                                tests[s.name] = (if (ok) "✓ " else "✗ ") + said
                                reload++
                            }
                        }) { Text("Test", color = Pal.link) }
                    }
                }
              }
            }
            item { Spacer(Modifier.size(24.dp)) }
        }
    }
}
