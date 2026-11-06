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

/** Money, the money dashboard and card benefits in one place, as tabs at the top. The last tab used is remembered. */
private val DASHBOARD_TABS = listOf("Accounts & bills", "Spending", "Card benefits")

@Composable
fun DashboardScreen(link: ServerLink?, onReports: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("dashboard", Context.MODE_PRIVATE) }
    var tab by remember { mutableStateOf(prefs.getInt("tab", 1).coerceIn(0, DASHBOARD_TABS.lastIndex)) }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            DASHBOARD_TABS.forEachIndexed { i, name ->
                FilterChip(selected = tab == i, onClick = { tab = i; prefs.edit().putInt("tab", i).apply() },
                    label = { Text(name) })
            }
        }
        Box(Modifier.fillMaxSize()) {
            key(tab) {  // a fresh page per tab: each WebView loads its own address once
                when (tab) {
                    0 -> MoneyScreen(link, onReports)
                    1 -> BuddyPage(link, "money.html")
                    else -> BuddyPage(link, "cards.html")
                }
            }
        }
    }
}
