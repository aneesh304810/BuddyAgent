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

/** Money: accounts and bills, spending, card benefits, investing and your email's groups in one place, as tabs at the
 *  top. The last tab used is remembered. With the redesign's layout ([NavLayout.v2]) Money always opens on Overview
 *  (accounts and bills) and Email lives in Profile. */
private val DASHBOARD_TABS = listOf("Accounts & bills", "Spending", "Cards", "Invest", "Email")
private val MONEY_TABS = listOf("Overview", "Spending", "Cards", "Invest")

@Composable
fun DashboardScreen(link: ServerLink?, onReports: () -> Unit) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("dashboard", Context.MODE_PRIVATE) }
    val tabs = if (NavLayout.v2) MONEY_TABS else DASHBOARD_TABS
    val jump = remember { Jump.money.also { Jump.money = null } }  // a quick action on Home (Home.kt)
    var tab by remember {
        mutableStateOf((jump ?: if (NavLayout.v2) 0 else prefs.getInt("tab", 1)).coerceIn(0, tabs.lastIndex))
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            tabs.forEachIndexed { i, name ->
                FilterChip(selected = tab == i, onClick = { tab = i; prefs.edit().putInt("tab", i).apply() },
                    label = { Text(name) })
            }
        }
        Box(Modifier.fillMaxSize()) {
            key(tab) {  // a fresh page per tab: each WebView loads its own address once
                when (tab) {
                    0 -> MoneyScreen(link, onReports)
                    1 -> BuddyPage(link, "money.html")
                    2 -> BuddyPage(link, "cards.html")
                    3 -> BuddyPage(link, "invest.html")  // strategy, practice, themes, watchlist
                    else -> BuddyPage(link, "mail.html")  // the kinds of email you get and what Buddy does with each
                }
            }
        }
    }
}
