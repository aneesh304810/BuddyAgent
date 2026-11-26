package app.buddy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate

/** Reading the date printed on a pack from what the phone's text reader saw (PackDate). */
class PackDateTest {
    private val today = LocalDate.of(2026, 11, 26)
    private fun read(text: String) = PackDate.parse(text, today)

    @Test
    fun usFormats() {
        assertEquals(LocalDate.of(2026, 12, 5), read("BEST BY 12/05/26"))
        assertEquals(LocalDate.of(2026, 12, 5), read("USE BY 12-05-2026"))
        assertEquals(LocalDate.of(2026, 12, 5), read("EXP 2026-12-05"))
        assertEquals(LocalDate.of(2026, 12, 5), read("BEST BEFORE DEC 05 2026"))
        assertEquals(LocalDate.of(2026, 12, 5), read("SELL BY 05 DEC 26"))
        assertEquals(LocalDate.of(2027, 1, 3), read("Best if used by\nJAN 03 2027"))
        assertEquals(LocalDate.of(2026, 12, 25), read("25/12/26"))  // a day over 12 comes first
    }

    @Test
    fun noYearMeansTheNextOne() {
        assertEquals(LocalDate.of(2026, 12, 2), read("USE BY DEC 02"))
        assertEquals(LocalDate.of(2027, 1, 9), read("BB JAN 9"))
    }

    @Test
    fun theMarkedDateWinsOverAPackedOnDate() {
        assertEquals(LocalDate.of(2026, 12, 20), read("PACKED 11/20/26 USE BY 12/20/26 L2345"))
        assertEquals(LocalDate.of(2026, 12, 20), read("11/20/26 12/20/26"))  // no marker: the later one
    }

    @Test
    fun theChickenLabel() {  // a store's own label: the sell-by date printed beside the price and the barcode
        val r = PackDate.read("THIGHS\nP-667 SELL BY 11.26.26\n2.99 2.62lb\nTOTAL PRICE $7.83", today)
        assertEquals(PackDate.Reading(LocalDate.of(2026, 11, 26), true), r)
    }

    @Test
    fun whileScanningAMarkedDateCountsAtOnceAnUnmarkedOneWhenReadTwice() {
        val w = DateWatch { today }
        assertEquals(LocalDate.of(2026, 12, 5), w.saw("BEST BY 12/05/26", 1L))
        assertEquals(LocalDate.of(2026, 12, 5), w.saw("12/20/26", 2L))  // unmarked: not over a marked one
        assertEquals(LocalDate.of(2026, 12, 5), w.saw("12/20/26", 3L))
        w.clear()
        assertNull(w.saw("12/20/26", 4L))                            // once: could be a stray number
        assertEquals(LocalDate.of(2026, 12, 20), w.saw("12/20/26", 5L))  // twice: taken
        assertEquals(5L, w.at)
        assertEquals(LocalDate.of(2026, 12, 20), w.saw("Net Wt 32 oz", 6L))  // nothing new: the date stays
    }

    @Test
    fun nothingThatLooksLikeAUseByDate() {
        assertNull(read("Net Wt 32 oz (2 lb) 907 g"))
        assertNull(read("12/05/19"))  // years ago
        assertNull(read("Lot 4417 1230"))
    }
}
