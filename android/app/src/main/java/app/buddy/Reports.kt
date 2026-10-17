package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class JobSummary(val id: Int, val title: String, val status: String, val createdAt: String)

data class JobReport(
    val id: Int,
    val title: String,
    val status: String,
    val summary: String,
    val report: String,
    val error: String,
)

/** Reads background job reports from your server (GET /api/jobs). */
object ReportsApi {
    private val http = OkHttpClient.Builder().callTimeout(30, TimeUnit.SECONDS).build()

    private suspend fun get(link: ServerLink, path: String): String = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(link.baseUrl + path)
            .header("Authorization", "Bearer ${link.token}")
            .build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                error(if (response.code == 401) "wrong token" else "server said ${response.code}")
            }
            response.body?.string() ?: ""
        }
    }

    suspend fun list(link: ServerLink): List<JobSummary> {
        val array = JSONArray(get(link, "/api/jobs"))
        return (0 until array.length()).map { i ->
            val job = array.getJSONObject(i)
            JobSummary(job.getInt("id"), job.optString("title"), job.optString("status"), job.optString("created_at"))
        }
    }

    suspend fun report(link: ServerLink, id: Int): JobReport {
        val job = JSONObject(get(link, "/api/jobs/$id"))
        fun text(key: String) = if (job.isNull(key)) "" else job.optString(key)
        return JobReport(id, text("title"), text("status"), text("summary"), text("report"), text("error"))
    }
}

@Composable
fun ReportsScreen(link: ServerLink?, onOpen: (Int) -> Unit) {
    var jobs by remember { mutableStateOf<List<JobSummary>?>(null) }
    var problem by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(link) {
        if (link == null) {
            problem = "Set up your server first."
            return@LaunchedEffect
        }
        runCatching { ReportsApi.list(link) }
            .onSuccess { jobs = it }
            .onFailure { problem = "Couldn't load reports (${it.message})." }
    }
    Column(Modifier.fillMaxSize()) {
        problem?.let { Text(it, color = Color(0xFFFF8A80)) }
        val list = jobs
        when {
            list == null && problem == null -> Text("Loading…", color = Color(0xFF8A93B2))
            list != null && list.isEmpty() -> Text(
                "No research yet. Ask Buddy: \"research the best phone plan in Brazil\".",
                color = Color(0xFF8A93B2),
            )
            list != null -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(list) { job ->
                    Column(
                        Modifier
                            .fillMaxWidth()
                            .background(Color(0xFF151C33), RoundedCornerShape(12.dp))
                            .clickable { onOpen(job.id) }
                            .padding(12.dp)
                    ) {
                        Text(job.title, color = Color.White, fontSize = 16.sp)
                        Text("${job.status} · ${job.createdAt.take(16).replace('T', ' ')} UTC", color = Color(0xFF8A93B2), fontSize = 12.sp)
                    }
                }
            }
        }
    }
}

@Composable
fun ReportScreen(link: ServerLink?, id: Int) {
    var report by remember(id) { mutableStateOf<JobReport?>(null) }
    var problem by remember(id) { mutableStateOf<String?>(null) }
    LaunchedEffect(link, id) {
        if (link == null) return@LaunchedEffect
        runCatching { ReportsApi.report(link, id) }
            .onSuccess { report = it }
            .onFailure { problem = "Couldn't load the report (${it.message})." }
    }
    val r = report
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        problem?.let { Text(it, color = Color(0xFFFF8A80)) }
        if (r == null) {
            if (problem == null) Text("Loading…", color = Color(0xFF8A93B2))
            return@Column
        }
        Text(r.title, color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.SemiBold)
        Text(
            if (r.status == "done") r.summary else "${r.status}: ${r.error}",
            color = Color.White, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(vertical = 10.dp),
        )
        SelectionContainer { Column { Markdown(r.report) } }
    }
}

private val LINK = Regex("""\[([^\]]+)\]\((https?://[^)\s]+)\)""")

/** Enough Markdown for a report: headings, bullets, tables, links. */
@Composable
private fun Markdown(md: String) {
    val uri = LocalUriHandler.current
    md.lines().forEach { raw ->
        val line = raw.trimEnd()
        when {
            line.isBlank() -> Text("", fontSize = 6.sp)
            line.startsWith("#") -> Text(
                clean(line.trimStart('#').trim()), color = Color.White,
                fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(top = 8.dp),
            )
            line.startsWith("|") -> {
                if (!Regex("""^\|[\s:|-]+\|$""").matches(line)) {
                    Text(
                        line.trim('|').split("|").joinToString("  ·  ") { it.trim() },
                        color = Color(0xFFE0E6FF), fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                    )
                }
            }
            else -> {
                val bullet = Regex("""^\s*(?:[-*]|\d+\.)\s+(.*)""").find(line)
                val body = bullet?.groupValues?.get(1) ?: line
                val link = LINK.find(body)
                Row {
                    if (bullet != null) Text("•  ", color = Color.White)
                    if (link != null && link.range.first == 0 && link.range.last == body.lastIndex) {
                        // A Sources line: open it in the browser.
                        Text(
                            link.groupValues[1], color = Color(0xFF8FB0FF),
                            textDecoration = TextDecoration.Underline,
                            modifier = Modifier.clickable { uri.openUri(link.groupValues[2]) },
                        )
                    } else {
                        Text(clean(LINK.replace(body) { "${it.groupValues[1]} (${it.groupValues[2]})" }), color = Color.White)
                    }
                }
            }
        }
    }
}

private fun clean(text: String) = text.replace("**", "").replace("__", "").replace("`", "")
