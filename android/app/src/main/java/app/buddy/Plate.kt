package app.buddy

/**
 * Several foods on one plate (Scan food → Dish), from the foods the owner taught: each camera frame is looked at
 * whole and in nine overlapping parts (DishEmbedder.parts), each part is matched to the taught foods (DishBook), and
 * a food counts as on the plate once it is seen in 2 of the last 5 frames, so one odd frame doesn't add it. Plain
 * Kotlin (no Android), so it can be checked off the phone.
 */
class PlateTally(private val window: Int = 5, private val need: Int = 2) {
    private val frames = ArrayDeque<Set<String>>()
    private val best = HashMap<String, Float>()

    /** One frame's foods (name to its best score over the parts); returns what is on the plate now, likeliest first. */
    fun add(found: Map<String, Float>): List<String> {
        frames.addLast(found.keys)
        while (frames.size > window) frames.removeFirst()
        found.forEach { (name, score) -> best[name] = maxOf(best[name] ?: 0f, score) }
        return onPlate()
    }

    fun onPlate(): List<String> =
        frames.flatten().groupingBy { it }.eachCount().filter { it.value >= need }.keys
            .sortedWith(compareByDescending<String> { best[it] ?: 0f }.thenBy { it })

    fun clear() {
        frames.clear()
        best.clear()
    }

    companion object {
        /** Each part's likeliest taught food (one food per part), and the best score of each food over the parts. */
        fun partsFound(matches: List<List<Pair<String, Float>>>): Map<String, Float> {
            val out = HashMap<String, Float>()
            for (part in matches) {
                val top = part.maxByOrNull { it.second } ?: continue
                out[top.first] = maxOf(out[top.first] ?: 0f, top.second)
            }
            return out
        }

        /** What goes back to the Health page: the names, one line, "|" between them (a name never holds "|"). */
        fun join(names: List<String>): String = names.map { it.replace("|", " ").trim() }.filter { it.isNotEmpty() }
            .distinct().take(8).joinToString("|")
    }
}
