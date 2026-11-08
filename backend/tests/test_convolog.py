"""The chat log: Claude sees the latest turns, older ones are found by search."""

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
