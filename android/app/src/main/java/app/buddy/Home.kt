package app.buddy

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Home, conversation first (buddy-redesign 03-ux, migration step 5): the conversation and its composer, with the six
 * things done most one tap away above it. On the open Fold the quick actions, anything waiting for a yes and today's
 * reminders and bills sit in a panel beside the conversation instead.
 */
enum class Quick(val label: String, val glyph: Glyph) {
    SCAN("Scan food", Glyph.SCAN), MEAL("Log a meal", Glyph.MEAL), BP("Log BP", Glyph.PULSE),
    SPENDING("Spending", Glyph.MONEY), BILLS("Bills", Glyph.DOC), DEALS("Deals", Glyph.TAG)
}

/** A section to open with the next screen: set by a quick action, read once by that screen. */
object Jump {
    var money: Int? = null      // a Money tab: 0 Overview (accounts and bills), 1 Spending
    var explore: Int? = null    // an Explore tab: 1 Deals
    var health: String? = null  // a Health page section ("food", "bp"), or "scan" to open the scanner
}

/** Where each quick action goes. Health ones open the Health page at that section; no AI model is involved. */
fun openQuick(q: Quick, onNavigate: (Screen) -> Unit) {
    when (q) {
        Quick.MEAL -> { Jump.health = "food"; onNavigate(Screen.Health) }
        Quick.BP -> { Jump.health = "bp"; onNavigate(Screen.Health) }
        Quick.SCAN -> { Jump.health = "scan"; onNavigate(Screen.Health) }
        Quick.SPENDING -> { Jump.money = 1; onNavigate(Screen.Dashboard) }
        Quick.BILLS -> { Jump.money = 0; onNavigate(Screen.Dashboard) }
        Quick.DEALS -> { Jump.explore = 1; onNavigate(Screen.Explore) }
    }
}

/** The quick actions: a row of chips above the conversation, or a grid of three across in the side panel. */
@Composable
fun QuickActions(grid: Boolean, onPick: (Quick) -> Unit) {
    if (grid) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Quick.entries.chunked(3).forEach { row ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.forEach { q ->
                        Column(
                            Modifier.weight(1f).heightIn(min = 76.dp).clip(RoundedCornerShape(14.dp)).background(Pal.panel)
                                .border(1.dp, Pal.track, RoundedCornerShape(14.dp))
                                .clickable(role = Role.Button) { onPick(q) }.padding(8.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center,
                        ) {
                            GlyphIcon(q.glyph, Pal.link, 22.dp)
                            Spacer(Modifier.height(6.dp))
                            Text(q.label, color = Pal.text, fontSize = 13.sp, textAlign = TextAlign.Center, maxLines = 2,
                                overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    } else {
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Quick.entries.forEach { q ->
                Row(
                    Modifier.heightIn(min = 48.dp).clip(RoundedCornerShape(50)).background(Pal.panel)
                        .border(1.dp, Pal.track, RoundedCornerShape(50))
                        .clickable(role = Role.Button) { onPick(q) }.padding(horizontal = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    GlyphIcon(q.glyph, Pal.link, 18.dp)
                    Spacer(Modifier.width(6.dp))
                    Text(q.label, color = Pal.text, fontSize = 14.sp, maxLines = 1)
                }
            }
        }
    }
}
