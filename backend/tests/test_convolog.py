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
