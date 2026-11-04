package app.buddy

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

private val Dim: Color get() = Pal.dim
private val Tile: Color get() = Pal.panel
private val Vault: Color get() = Pal.warn

/** Buddy's library on the server: /api/library and /api/people. */
object LibraryApi {
    private val http = OkHttpClient.Builder().callTimeout(60, TimeUnit.SECONDS).build()

    private suspend fun call(link: ServerLink, path: String, method: String = "GET"): ByteArray =
        withContext(Dispatchers.IO) {
            val builder = Request.Builder().url(link.baseUrl + path).header("Authorization", "Bearer ${link.token}")
            when (method) {
                "POST" -> builder.post(ByteArray(0).toRequestBody())
                "DELETE" -> builder.delete()
            }
            http.newCall(builder.build()).execute().use { r ->
                if (!r.isSuccessful) {
                    val detail = runCatching { JSONObject(r.body?.string().orEmpty()).optString("detail") }.getOrNull()
                    error(detail?.ifBlank { null } ?: "server said ${r.code}")
                }
                r.body?.bytes() ?: ByteArray(0)
            }
        }

    suspend fun list(link: ServerLink, query: String): JSONObject {
        val q = if (query.isBlank()) "" else "?q=" + URLEncoder.encode(query, "UTF-8")
        return JSONObject(String(call(link, "/api/library$q")))
    }

    suspend fun item(link: ServerLink, id: String) = JSONObject(String(call(link, "/api/library/$id")))
    suspend fun brain(link: ServerLink) = JSONObject(String(call(link, "/api/brain")))
    suspend fun forgetPreference(link: ServerLink, id: Int) = call(link, "/api/brain/preferences/$id", "DELETE")
    suspend fun unlock(link: ServerLink, id: String) = call(link, "/api/library/$id/unlock", "POST")
    suspend fun forget(link: ServerLink, id: String) = call(link, "/api/library/$id", "DELETE")

    /** The original file, saved to the app's cache so another app can show it. */
    suspend fun download(context: Context, link: ServerLink, id: String, name: String): File {
        val bytes = call(link, "/api/library/$id/file")
        return withContext(Dispatchers.IO) {
            val dir = File(context.cacheDir, "library").apply { mkdirs() }
            dir.listFiles()?.forEach { it.delete() }  // only the one being looked at stays on the phone
            File(dir, name.replace(Regex("[/\\\\]"), "_")).apply { writeBytes(bytes) }
        }
    }
}

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

@Composable
fun LibraryScreen(link: ServerLink?, onOpen: (String) -> Unit) {
    var query by remember { mutableStateOf("") }
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    LaunchedEffect(link, query, refresh) {
        if (link == null) {
            problem = "Set up your server first."
            return@LaunchedEffect
        }
        if (query.isNotBlank()) delay(400)  // search once typing pauses
        runCatching { LibraryApi.list(link, query) }
            .onSuccess { data = it; problem = null }
            .onFailure { problem = "Couldn't load the library (${it.message})." }
    }
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        OutlinedTextField(
            value = query, onValueChange = { query = it }, singleLine = true,
            placeholder = { Text("Search by meaning: \"car insurance\", \"my lease\"…") },
            modifier = Modifier.fillMaxWidth(),
        )
        problem?.let { Text(it, color = Pal.bad) }
        if (query.isBlank() && link != null) BrainPanel(link)
        val people = data?.optJSONArray("people")?.objects().orEmpty()
        if (people.isNotEmpty()) {
            Text("People and things", color = Dim, fontSize = 13.sp)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                people.take(20).forEach { p ->
                    val relation = p.optString("relation")
                    Text(p.optString("name") + if (relation.isNotBlank()) " · $relation" else "",
                        color = Pal.text, fontSize = 13.sp,
                        modifier = Modifier.background(Tile, RoundedCornerShape(50))
                            .clickable { query = p.optString("name") }.padding(horizontal = 12.dp, vertical = 6.dp))
                }
            }
        }
        val items = data?.optJSONArray("items")?.objects()
        when {
            items == null && problem == null -> Text("Loading…", color = Dim)
            items != null && items.isEmpty() -> Text(
                if (query.isBlank()) "Nothing saved yet. Use the 📎 in the chat, or Share → Save to Buddy from any app."
                else "Nothing matches.", color = Dim)
            items != null -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(items) { item ->
                    Column(
                        Modifier.fillMaxWidth().background(Tile, RoundedCornerShape(12.dp))
                            .clickable { onOpen(item.optString("id")) }.padding(12.dp),
                    ) {
                        Row {
                            Text((if (item.optBoolean("vault")) "🔒 " else "") +
                                item.optString("title").ifBlank { item.optString("file") },
                                color = Pal.text, fontSize = 16.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f))
                        }
                        Text(listOf(item.optString("type"), item.optString("file"), item.optString("added"))
                            .filter { it.isNotBlank() }.joinToString(" · "), color = Dim, fontSize = 12.sp, maxLines = 1)
                        item.optString("match").takeIf { it.isNotBlank() }?.let {
                            Text(it, color = Pal.soft, fontSize = 13.sp, maxLines = 3,
                                overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun LibraryItemScreen(link: ServerLink?, id: String, onGone: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var item by remember(id) { mutableStateOf<JSONObject?>(null) }
    var problem by remember(id) { mutableStateOf<String?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    var confirmForget by remember { mutableStateOf(false) }
    LaunchedEffect(link, id, refresh) {
        if (link == null) return@LaunchedEffect
        runCatching { LibraryApi.item(link, id) }
            .onSuccess { item = it; problem = null }
            .onFailure { problem = "Couldn't open it (${it.message})." }
    }
    val it = item
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        problem?.let { p -> Text(p, color = Pal.bad) }
        if (it == null || link == null) {
            if (problem == null) Text("Loading…", color = Dim)
            return@Column
        }
        val locked = it.optBoolean("locked")
        Text((if (locked) "🔒 " else "") + it.optString("title").ifBlank { it.optString("file") },
            color = Pal.text, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
        Text(listOf(it.optString("type"), it.optString("file"), "added " + it.optString("added"))
            .filter { s -> s.isNotBlank() }.joinToString(" · "), color = Dim, fontSize = 13.sp)
        if (it.optString("note").isNotBlank()) Text("Your note: " + it.optString("note"), color = Pal.soft)
        if (it.optString("summary").isNotBlank()) Text(it.optString("summary"), color = Pal.text)
        val facts = it.optJSONArray("facts")
        if (facts != null && facts.length() > 0) {
            Text("Facts", color = Dim, fontSize = 13.sp)
            (0 until facts.length()).forEach { i -> Text("• " + facts.getString(i), color = Pal.text, fontSize = 14.sp) }
        }
        it.optJSONArray("tables")?.objects()?.forEach { t ->
            Text("Sheet ${t.optString("sheet")}: ${t.optInt("rows")} rows · ${t.optJSONArray("columns")?.join(", ")?.replace("\"", "")}",
                color = Pal.soft, fontSize = 13.sp)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (locked) OutlinedButton(onClick = {
                scope.launch {
                    runCatching { LibraryApi.unlock(link, id) }.onSuccess { refresh++ }
                        .onFailure { e -> problem = "Couldn't unlock (${e.message})." }
                }
            }) { Text("Unlock 10 min") }
            OutlinedButton(onClick = {
                scope.launch {
                    runCatching {
                        val file = LibraryApi.download(context, link, id, it.optString("file"))
                        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
                        context.startActivity(Intent.createChooser(
                            Intent(Intent.ACTION_VIEW).setDataAndType(uri, context.contentResolver.getType(uri))
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), it.optString("file")))
                    }.onFailure { e -> problem = "Couldn't open the file (${e.message})." }
                }
            }, enabled = !locked) { Text("Open file") }
            TextButton(onClick = { confirmForget = true }) { Text("Forget", color = Pal.bad) }
        }
        if (!locked && it.optString("text").isNotBlank()) {
            Spacer(Modifier.height(4.dp))
            Text("What's in it", color = Dim, fontSize = 13.sp)
            SelectionContainer { Text(it.optString("text"), color = Pal.soft, fontSize = 13.sp) }
            it.optString("more").takeIf { m -> m.isNotBlank() }?.let { m -> Text("($m)", color = Dim, fontSize = 12.sp) }
        } else if (locked) {
            Text("In the vault: unlock to see the text, or ask Buddy (it asks before reading).", color = Vault, fontSize = 13.sp)
        }
    }
    if (confirmForget) AlertDialog(
        onDismissRequest = { confirmForget = false },
        title = { Text("Forget this?") },
        text = { Text("Buddy deletes the file, its text and the facts it learned from it.") },
        confirmButton = {
            TextButton(onClick = {
                confirmForget = false
                scope.launch {
                    runCatching { LibraryApi.forget(link ?: return@launch, id) }.onSuccess { onGone() }
                        .onFailure { e -> problem = "Couldn't forget it (${e.message})." }
                }
            }) { Text("Forget") }
        },
        dismissButton = { TextButton(onClick = { confirmForget = false }) { Text("Keep") } },
    )
}


/** What Buddy has learned about you: preferences (tap ✕ to forget one), dates it watches, questions waiting. */
@Composable
private fun BrainPanel(link: ServerLink) {
    var open by remember { mutableStateOf(false) }
    var brain by remember { mutableStateOf<JSONObject?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(open, refresh) {
        if (open) brain = runCatching { LibraryApi.brain(link) }.getOrNull()
    }
    Text((if (open) "▾ " else "▸ ") + "What Buddy knows about you", color = Pal.text, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.fillMaxWidth().clickable { open = !open }.padding(vertical = 4.dp))
    if (!open) return
    val b = brain ?: run { Text("Loading…", color = Dim); return }
    Column(Modifier.background(Tile, RoundedCornerShape(12.dp)).padding(12.dp).fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val prefs = b.optJSONArray("preferences")?.objects().orEmpty()
        Text("How you like things", color = Dim, fontSize = 13.sp)
        if (prefs.isEmpty()) Text("Nothing yet: tell Buddy (\"I prefer aisle seats\") or it learns from your choices.",
            color = Dim, fontSize = 13.sp)
        prefs.take(30).forEach { p ->
            Row(Modifier.fillMaxWidth()) {
                Text(p.optString("topic") + ": " + p.optString("text") +
                    if (p.optString("source") == "learned") "  (learned)" else "",
                    color = Pal.text, fontSize = 14.sp, modifier = Modifier.weight(1f))
                Text("✕", color = Dim, modifier = Modifier.clickable {
                    scope.launch { runCatching { LibraryApi.forgetPreference(link, p.optInt("id")) }; refresh++ }
                }.padding(horizontal = 8.dp))
            }
        }
        val dates = b.optJSONArray("dates")?.objects().orEmpty()
        if (dates.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text("Dates I'm watching (reminders set ahead)", color = Dim, fontSize = 13.sp)
            dates.take(10).forEach { d ->
                Text(d.optString("date") + "  " + d.optString("what"), color = Pal.text, fontSize = 14.sp)
            }
        }
        val questions = b.optJSONArray("questions")?.objects().orEmpty()
        if (questions.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text("Questions for you (answer Buddy in the chat)", color = Vault, fontSize = 13.sp)
            questions.forEach { q -> Text(q.optString("question"), color = Pal.text, fontSize = 14.sp) }
        }
        val status = b.optJSONObject("status")
        if (status != null) {
            Text("${status.optInt("facts")} facts · ${status.optInt("passages")} passages · " +
                "${status.optInt("people_and_things")} people and things · last tidy-up: ${status.optString("last_night")}",
                color = Dim, fontSize = 12.sp)
        }
    }
}
