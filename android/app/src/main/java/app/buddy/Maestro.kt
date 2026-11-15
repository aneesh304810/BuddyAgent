package app.buddy

import android.content.Context
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/** Buddy Maestro: the AI providers and which does each job (web/maestro.html), and every model request Buddy made
 *  (Spending: the web view with the drill-down; Requests: what used to be the Insights screen), as tabs at the top. The last tab used is remembered. */
private val MAESTRO_TABS = listOf("Providers & models", "AI spending", "Requests")

@Composable
fun MaestroScreen(link: ServerLink?, openUsage: Boolean = false) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("maestro", Context.MODE_PRIVATE) }
    var tab by remember {
        mutableStateOf(if (openUsage) 1 else prefs.getInt("tab", 0).coerceIn(0, MAESTRO_TABS.lastIndex))
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            MAESTRO_TABS.forEachIndexed { i, name ->
                FilterChip(selected = tab == i, onClick = { tab = i; prefs.edit().putInt("tab", i).apply() },
                    label = { Text(name) })
            }
        }
        Box(Modifier.fillMaxSize()) {
            key(tab) {  // a fresh page per tab: the WebView loads its address once
                when (tab) {
                    0 -> BuddyPage(link, "maestro.html", "view=providers")
                    1 -> BuddyPage(link, "maestro.html", "view=usage")  // totals, by component, drill-down
                    else -> InsightsScreen(link)                         // every request, speed, voice, tools
                }
            }
        }
    }
}
