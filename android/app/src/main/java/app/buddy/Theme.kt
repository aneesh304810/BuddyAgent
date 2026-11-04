package app.buddy

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color

/**
 * Buddy's two looks, chosen in Settings → Look:
 *  - Default: the dark look (night blue, soft blue accents).
 *  - Light: the buddylabs.io website's look: white, near-black text, Buddy green (#1F6F5C), amber (#C8862A).
 * Every screen takes its colours from [Pal]; changing [Theme.light] redraws the whole app at once.
 * The light colours keep text readable on white (4.5:1 or better), so amber and red are deeper than the dark ones.
 */
object Theme {
    var light by mutableStateOf(false)
    const val DEFAULT = "default"
    const val LIGHT = "light"
}

object Pal {
    private fun pick(dark: Long, light: Long) = Color(if (Theme.light) light else dark)

    val bg get() = pick(0xFF0B1020, 0xFFFFFFFF)          // the screen
    val panel get() = pick(0xFF151C33, 0xFFF6F4EF)       // tiles, bubbles, cards
    val track get() = pick(0xFF222B47, 0xFFE6E3DC)       // bar tracks, unselected chips, lines
    val placeholder get() = pick(0xFF1E2747, 0xFFE5F2EE) // empty image / map tiles
    val text get() = pick(0xFFFFFFFF, 0xFF16181D)
    val bright get() = pick(0xFFE0E6FF, 0xFF2A2E36)
    val soft get() = pick(0xFFC9D3E3, 0xFF3C424D)
    val dim get() = pick(0xFF8A93B2, 0xFF5B616E)
    val accent get() = pick(0xFF5B8CFF, 0xFF1F6F5C)
    val link get() = pick(0xFF8FB0FF, 0xFF1F6F5C)
    val bar get() = pick(0xFF36C5F0, 0xFF1F6F5C)
    val warn get() = pick(0xFFFFC46B, 0xFF8A5A12)
    val bad get() = pick(0xFFFF8A80, 0xFFB3261E)
    val info get() = pick(0xFFFFA8A0, 0xFFB3261E)
    val good get() = pick(0xFF7BE0A5, 0xFF1B7A4B)
    val listening get() = pick(0xFF3DDC97, 0xFF1F8A5B)
    val talking get() = pick(0xFFFFB547, 0xFFC8862A)
    val thinking get() = pick(0xFFB98CFF, 0xFF7B5CC4)
    val chip get() = pick(0xFFFFFFFF, 0xFF1F6F5C)        // a selected chip
    val chipSoft get() = pick(0xFFE0E6FF, 0xFF1F6F5C)    // the selected half of Talk | Listen-in
    val onChip get() = pick(0xFF000000, 0xFFFFFFFF)      // text on a selected chip or a coloured button

    /** The money dashboard page's background, before it loads. */
    val pageBg get() = if (Theme.light) "#FFFFFF" else "#0E1222"
}
