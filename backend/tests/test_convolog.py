"""The chat log: Claude sees the latest turns, older ones are found by search."""

from datetime import datetime, timedelta, timezone

from buddy import agent, convolog
from buddy.memory import Memory


def test_search_finds_an_old_report(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "assistant", "Your weekly money report: you spent $412, groceries up 20%.")
    convolog.log(m, "user", "what's the weather tomorrow")
    convolog.log(m, "user", "(Buddy spoke up on its own; no question asked.)")  # the note isn't worth keeping
    found = convolog.search(m, "the money report you sent")
    assert found[0]["text"].startswith("Your weekly money report")
    assert "weekly money report" in convolog.run_tool(m, {"query": "money report"})
    assert "Nothing" in convolog.run_tool(m, {"query": "pathology"})
    assert all("spoke up" not in f["text"] for f in convolog.search(m, ""))
    m.db.execute("UPDATE convo_log SET at = '2020-01-01T00:00:00+00:00'")
    assert convolog.prune(m) == 2 and convolog.search(m, "money") == []


def test_tool_results_are_not_kept():
    msg = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "secret rows"}]}
    assert convolog.text_of(msg) == ""


def test_trimmed_turns_stay_searchable_and_are_logged_once(tmp_path):
    m = Memory(tmp_path / "b.db")
    c = agent.Conversation.__new__(agent.Conversation)
    c.memory = m
    c.messages = []
    for i in range(30):
        c.messages += [{"role": "user", "content": f"question {i} about topic{i}"},
                       {"role": "assistant", "content": f"answer {i}"}]
        c._trim_history()
        c.messages = [dict(x) for x in c.messages]  # repair copies them: still logged once
    assert len(c.messages) <= agent.MAX_HISTORY
    assert convolog.search(m, "topic0")[0]["text"] == "question 0 about topic0"
    n = m.db.execute("SELECT COUNT(*) FROM convo_log WHERE text = 'question 3 about topic3'").fetchone()[0]
    assert n == 1


def test_recall_tool_is_always_offered():
    assert "recall_conversation" in agent.ALWAYS_LOADED


def test_word_forms_match(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "assistant", "I sent two reports about your spending this week.")
    assert convolog.search(m, "report spend")[0]["text"].startswith("I sent two reports")


def test_old_index_is_rebuilt_with_word_forms(tmp_path):
    m = Memory(tmp_path / "b.db")
    m.db.executescript("""CREATE TABLE convo_log (id INTEGER PRIMARY KEY, at TEXT NOT NULL, role TEXT NOT NULL,
        text TEXT NOT NULL);
        CREATE VIRTUAL TABLE convo_fts USING fts5(text, content='convo_log', content_rowid='id');""")
    m.db.execute("INSERT INTO convo_log (at, role, text) VALUES (?, 'user', 'the soccer reminders')",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    m.db.commit()
    assert convolog.search(m, "reminder")[0]["text"] == "the soccer reminders"


def test_lines_around_a_match_come_back(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "user", "my car makes a grinding sound when I stop")
    convolog.log(m, "assistant", "Sounds like worn brakes; book the dealer.")
    convolog.log(m, "user", "ok")
    convolog.log(m, "user", "weather?")
    out = convolog.run_tool(m, {"query": "brakes dealer"})
    assert "grinding sound" in out and "> [" in out and "weather" not in out


def test_newer_wins_when_equally_good(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "user", "dinner plan at the italian place")
    convolog.log(m, "user", "dinner plan at the thai place")
    m.db.execute("UPDATE convo_log SET at = ? WHERE text LIKE '%italian%'",
                 ((datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds"),))
    m.db.commit()
    hits = sorted((f for f in convolog.search(m, "dinner plan", days=30, around=0) if f["hit"]), key=lambda f: f["rank"])
    assert "thai" in hits[0]["text"]


def _at(m, text, when):
    m.db.execute("UPDATE convo_log SET at = ? WHERE text = ?", (when.astimezone(timezone.utc).isoformat(), text))
    m.db.commit()


def test_time_words_become_a_range(monkeypatch):
    from zoneinfo import ZoneInfo
    from buddy import tools
    monkeypatch.setattr(tools, "_user_tz", ZoneInfo("America/New_York"))
    now = datetime(2026, 11, 11, 15, 0, tzinfo=ZoneInfo("America/New_York"))  # a Wednesday afternoon
    a, b = convolog.time_range("what did you say yesterday morning", now)
    assert a.astimezone(now.tzinfo) == datetime(2026, 11, 10, 5, 0, tzinfo=now.tzinfo)
    assert b.astimezone(now.tzinfo) == datetime(2026, 11, 10, 12, 0, tzinfo=now.tzinfo)
    a, b = convolog.time_range("on Monday", now)
    assert a.astimezone(now.tzinfo).day == 9 and b.astimezone(now.tzinfo).day == 10
    a, _ = convolog.time_range("last week", now)
    assert a.astimezone(now.tzinfo).day == 2  # the Monday before
    a, _ = convolog.time_range("last night", now)
    assert a.astimezone(now.tzinfo) == datetime(2026, 11, 10, 18, 0, tzinfo=now.tzinfo)
    assert convolog.time_range("the soccer plan", now) is None


def test_time_narrows_the_search(tmp_path):
    m = Memory(tmp_path / "b.db")
    now = datetime.now(timezone.utc)
    convolog.log(m, "user", "dinner at the italian place")
    convolog.log(m, "user", "dinner at the thai place")
    _at(m, "dinner at the italian place", now - timedelta(days=1))
    out = convolog.run_tool(m, {"query": "dinner", "when": "yesterday"})
    assert "italian" in out and "thai" not in out


def test_common_words_dont_match_everything(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "user", "what did you think about it")
    convolog.log(m, "assistant", "The soccer practice moved to Saturday.")
    hits = [f for f in convolog.search(m, "what did you say about soccer", around=0) if f["hit"]]
    assert [h["text"] for h in hits] == ["The soccer practice moved to Saturday."]


def test_lookup_before_the_reply_only_when_pointing_back(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "assistant", "Your weekly money report: spent $412, groceries up 20%.")
    found = convolog.auto_context(m, "what was in that report you sent?")
    assert found.startswith(convolog.AUTO_MARK) and "weekly money report" in found
    assert convolog.auto_context(m, "write me a money report") == ""  # nothing points back
    in_view = {"Your weekly money report: spent $412, groceries up 20%."}
    assert convolog.auto_context(m, "what was in that report you sent?", in_view) == ""  # already in view


def test_conversation_attaches_the_lookup_and_logs_only_the_words(tmp_path):
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "assistant", "Your weekly money report: spent $412.")
    c = agent.Conversation.__new__(agent.Conversation)
    c.memory, c.messages = m, []
    c._said("(Sunday 08 November 2026, 09:00 AM) what was that report you sent", "what was that report you sent")
    c.messages.append({"role": "user", "content": "(Sunday 08 November 2026, 09:00 AM) what was that report you sent"
                       "\n\n(Buddy's maps: mom's lab results)"})
    c._attach_earlier("what was that report you sent")
    assert any(b["text"].startswith(convolog.AUTO_MARK) for b in c.messages[-1]["content"])
    c.log_new_messages()
    last = m.db.execute("SELECT text FROM convo_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert last == "what was that report you sent"  # no time stamp, no maps, no attached lines


def test_listen_in_whispers_are_not_kept(tmp_path):
    m = Memory(tmp_path / "b.db")
    c = agent.Conversation.__new__(agent.Conversation)
    c.memory, c.messages = m, [{"role": "assistant", "content": "hi"}]
    c.note_proactive("They said the meeting moved to 3 PM.", "listen_in")
    c.note_proactive("Your package arrives today.", "reminder")
    c.log_new_messages()
    kept = [r[0] for r in m.db.execute("SELECT text FROM convo_log")]
    assert "Your package arrives today." in kept and not any("meeting moved" in k for k in kept)


def test_old_turns_leave_a_topic_line(tmp_path):
    m = Memory(tmp_path / "b.db")
    c = agent.Conversation.__new__(agent.Conversation)
    c.memory, c.messages = m, []
    c.note_proactive("Your weekly money report: spent $412, groceries up.", "money")
    c.messages += [{"role": "user", "content": "what about the car brake noise at the dealer"},
                   {"role": "assistant", "content": "Book a check."}]
    for i in range(12):
        c.messages += [{"role": "user", "content": f"question number {i} about things"},
                       {"role": "assistant", "content": f"answer {i}"}]
    c._trim_history()
    first = c.messages[0]["content"][0]["text"]
    assert first.startswith(convolog.EARLIER_MARK)
    assert "you sent: Your weekly money report" in first and "car brake noise" in first
    assert convolog.text_of(c.messages[0]) == c.messages[0]["content"][1]["text"]  # the line itself isn't logged


def test_forget_removes_chat_lines(tmp_path):
    from buddy.tools import run_tool
    m = Memory(tmp_path / "b.db")
    convolog.log(m, "user", "my old password hint was blue horse")
    convolog.log(m, "user", "weather?")
    assert "1 line" in run_tool(m, "forget_fact", {"text": "blue horse"})
    assert convolog.search(m, "horse") == [] and convolog.search(m, "weather")
