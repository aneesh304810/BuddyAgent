package app.buddy

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

enum class Mode { TALK, LISTEN_IN }

enum class Speaker { YOU, BUDDY, NUDGE, WHISPER, ERROR, STATUS }

/** One bubble. `jobId` links a "your research is ready" nudge to its report; `turn` lets
 *  you rate a reply (thumbs up 1, down -1). */
data class Line(
    val text: String,
    val from: Speaker,
    val jobId: Int? = null,
    val turn: String? = null,
    val rating: Int? = null,
    val opensInsights: Boolean = false,  // the daily health digest: tap for the dashboard
    val cards: String? = null,           // JSON array: places, stays, flights, news... under the reply
    val kind: String = "",               // nudges: reminder, job_done, health...
    val at: Long = System.currentTimeMillis(),
)

data class UiState(
    val connection: String = "offline",
    val online: Boolean = false,
    val running: Boolean = false,
    val listening: Boolean = false,
    val speaking: Boolean = false,
    val thinking: Boolean = false,    // the server has the question and is working on it
    val conversationOpen: Boolean = false,  // Listen-in: a follow-up now needs no "Hey Buddy"
    val onCall: Boolean = false,
    val mode: Mode = Mode.TALK,
    val lines: List<Line> = emptyList(),
    val approval: String? = null,     // Buddy is waiting for a yes: show Approve / Decline
    // Voice match (Talk mode answers only your voice), from the server.
    val voiceInstalled: Boolean = false,
    val voiceEnrolled: Boolean = false,
    val voiceOn: Boolean = false,
    val voiceSentences: List<String> = emptyList(),
    val enrollStep: Int = -1,         // -1 idle; 0..n-1 recording sentence; n: making the voiceprint
    val enrollResult: String? = null,
)

/** What the screen shows. The service writes it; the activity only reads it. */
object BuddyState {
    private val state = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = state

    fun update(change: (UiState) -> UiState) = state.update(change)

    fun add(line: Line) = update { it.copy(lines = (it.lines + line).takeLast(200)) }

    /** The reply just finished: tag its bubble with the turn so it can be rated. */
    fun replyDone(turn: String) = update {
        val last = it.lines.lastOrNull()
        if (turn.isEmpty() || last?.from != Speaker.BUDDY || last.turn != null) it
        else it.copy(lines = it.lines.dropLast(1) + last.copy(turn = turn))
    }

    /** Cards arrive just before the reply ends: they go under its bubble. */
    fun attachCards(json: String) = update {
        val last = it.lines.lastOrNull()
        if (last?.from == Speaker.BUDDY && last.turn == null) {
            it.copy(lines = it.lines.dropLast(1) + last.copy(cards = json))
        } else {
            it.copy(lines = (it.lines + Line("", Speaker.BUDDY, cards = json)).takeLast(200))
        }
    }

    fun rated(turn: String, rating: Int?) = update {
        it.copy(lines = it.lines.map { line -> if (line.turn == turn) line.copy(rating = rating) else line })
    }

    /** "Hang on, checking": a status line while tools work, replaced by the answer. */
    fun status(text: String) = update {
        val lines = if (it.lines.lastOrNull()?.from == Speaker.STATUS) it.lines.dropLast(1) else it.lines
        it.copy(lines = (lines + Line(text, Speaker.STATUS)).takeLast(200))
    }

    /** Buddy speaks sentence by sentence; join them into one bubble per reply. */
    fun buddySentence(text: String, newReply: Boolean) = update { before ->
        val it = if (before.lines.lastOrNull()?.from == Speaker.STATUS) before.copy(lines = before.lines.dropLast(1)) else before
        val last = it.lines.lastOrNull()
        if (newReply || last?.from != Speaker.BUDDY) {
            it.copy(lines = (it.lines + Line(text, Speaker.BUDDY)).takeLast(200))
        } else {
            it.copy(lines = it.lines.dropLast(1) + last.copy(text = last.text + " " + text))
        }
    }
}
