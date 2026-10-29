package app.buddy

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

enum class Mode { TALK, LISTEN_IN }

enum class Speaker { YOU, BUDDY, NUDGE, WHISPER, ERROR }

/** One bubble. `jobId` links a "your research is ready" nudge to its report. */
data class Line(val text: String, val from: Speaker, val jobId: Int? = null)

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
)

/** What the screen shows. The service writes it; the activity only reads it. */
object BuddyState {
    private val state = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = state

    fun update(change: (UiState) -> UiState) = state.update(change)

    fun add(line: Line) = update { it.copy(lines = (it.lines + line).takeLast(200)) }

    /** Buddy speaks sentence by sentence; join them into one bubble per reply. */
    fun buddySentence(text: String, newReply: Boolean) = update {
        val last = it.lines.lastOrNull()
        if (newReply || last?.from != Speaker.BUDDY) {
            it.copy(lines = (it.lines + Line(text, Speaker.BUDDY)).takeLast(200))
        } else {
            it.copy(lines = it.lines.dropLast(1) + last.copy(text = last.text + " " + text))
        }
    }
}
