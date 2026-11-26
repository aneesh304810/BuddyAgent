package app.buddy

import java.time.LocalDate

/**
 * The date printed on a pack, from the words the phone's text reader saw (ML Kit, on the phone; no picture is kept or
 * sent). US packs print month first: 12/05/26, 12-05-2026, DEC 05 2026, 05 DEC 26, 2026-12-05; "BEST BY", "USE BY",
 * "EXP" and "SELL BY" mark the one that counts. A pack can also carry a packed-on date: without a marker, the latest
 * date from a month ago to three years ahead is taken, since a packing date comes before the use-by date.
 */
object PackDate {
    private val months = mapOf(
        "JAN" to 1, "FEB" to 2, "MAR" to 3, "APR" to 4, "MAY" to 5, "JUN" to 6, "JUL" to 7, "AUG" to 8,
        "SEP" to 9, "OCT" to 10, "NOV" to 11, "DEC" to 12,
    )
    private val marker = Regex("""\b(BEST\s*(BEFORE|BY|IF\s+USED\s+BY)|USE\s*BY|EXP(IRES|IRY)?|SELL\s*BY|BB)\b""")
    private val numeric = Regex("""\b(\d{1,4})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\b""")
    private val wordy = Regex(
        """(?:\b(\d{1,2})\s*)?\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?(?:\s*(\d{1,2})\b)?(?:\s*,?\s*(\d{4}|\d{2})\b)?"""
    )

    private fun year(y: String): Int? = when (y.length) {
        0 -> null
        2 -> 2000 + y.toInt()
        4 -> y.toInt()
        else -> null
    }

    private fun make(y: Int?, m: Int, d: Int, today: LocalDate): LocalDate? {
        if (m !in 1..12 || d !in 1..31) return null
        return runCatching {
            if (y != null) LocalDate.of(y, m, d) else {
                val thisYear = LocalDate.of(today.year, m, d)
                if (thisYear.isBefore(today.minusDays(30))) thisYear.plusYears(1) else thisYear
            }
        }.getOrNull()
    }

    private data class Found(val date: LocalDate, val at: Int)

    private fun candidates(text: String, today: LocalDate): List<Found> {
        val out = mutableListOf<Found>()
        for (m in numeric.findAll(text)) {
            val (a, b, c) = m.destructured
            val date = if (a.length == 4) make(a.toInt(), b.toInt(), c.toInt(), today)  // 2026-12-05
            else if (a.length > 2 || c.length == 3) null
            else if (a.toInt() > 12) make(year(c), b.toInt(), a.toInt(), today)       // 25/12/26: day first
            else make(year(c), a.toInt(), b.toInt(), today)                           // 12/05/26: month first
            if (date != null) out += Found(date, m.range.first)
        }
        for (m in wordy.findAll(text)) {
            val (before, name, after, y) = m.destructured
            val month = months[name] ?: continue
            // 05 DEC 26: day before, year after. DEC 05 2026 / DEC 05: day after. 05 DEC 2026: day before.
            val (dayText, yearText) = when {
                before.isNotBlank() && after.isNotBlank() && y.isBlank() -> before to after
                before.isNotBlank() && after.isBlank() -> before to y
                else -> after to y
            }
            if (dayText.isBlank()) continue
            make(year(yearText), month, dayText.toInt(), today)?.let { out += Found(it, m.range.first) }
        }
        return out.filter { !it.date.isBefore(today.minusDays(30)) && !it.date.isAfter(today.plusYears(3)) }
    }

    /** A date read, and whether a marker (BEST BY, USE BY, EXP, SELL BY) stood just before it. */
    data class Reading(val date: LocalDate, val marked: Boolean)

    /** The use-by date in what the text reader saw, with whether it was marked, or null. */
    fun read(seen: String, today: LocalDate = LocalDate.now()): Reading? {
        val text = seen.uppercase()
        val found = candidates(text, today)
        if (found.isEmpty()) return null
        val marks = marker.findAll(text).map { it.range.last }.toList()
        val marked = found.filter { f -> marks.any { it < f.at && f.at - it <= 24 } }
        return Reading(marked.ifEmpty { found }.maxOf { it.date }, marked.isNotEmpty())
    }

    /** The use-by date in what the text reader saw, or null. */
    fun parse(seen: String, today: LocalDate = LocalDate.now()): LocalDate? = read(seen, today)?.date
}

/**
 * The date on a pack, from frame after frame of what the text reader sees while scanning (several a second): a marked
 * date ("SELL BY 11.26.26") counts at once, an unmarked one once the same date is read twice, so a stray number is not
 * taken; a marked date isn't replaced by an unmarked one. [at] is when it was last read (elapsed milliseconds).
 */
class DateWatch(private val today: () -> LocalDate = { LocalDate.now() }) {
    var date: LocalDate? = null
        private set
    var at = 0L
        private set
    private var marked = false
    private var candidate: LocalDate? = null
    private var count = 0

    fun saw(text: String, now: Long): LocalDate? {
        val r = PackDate.read(text, today()) ?: return date
        if (r.date == candidate) count += 1 else { candidate = r.date; count = 1 }
        if ((r.marked || count >= 2) && (r.marked || !marked)) {
            date = r.date
            marked = r.marked
            at = now
        } else if (r.date == date) {
            at = now  // still in view
        }
        return date
    }

    fun clear() {
        date = null; at = 0L; marked = false; candidate = null; count = 0
    }
}
