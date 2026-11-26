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
    fun nothingThatLooksLikeAUseByDate() {
        assertNull(read("Net Wt 32 oz (2 lb) 907 g"))
        assertNull(read("12/05/19"))  // years ago
        assertNull(read("Lot 4417 1230"))
    }
}
