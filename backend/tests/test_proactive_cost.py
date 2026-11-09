"""Background writing uses the quick model and a small prompt, not the full chat prompt on the premium model."""

import asyncio
from datetime import timedelta

from buddy import money, proactive
from buddy.config import settings
from buddy.memory import Memory
from buddy.tools import local_now


def _capture(monkeypatch):
    seen = {}

    async def compose(system, instruction, memory=None, *, label, model=None):
        seen.update(system=system, label=label, model=model)
        return "Good morning!"
    monkeypatch.setattr(proactive, "compose", compose)
    return seen


def test_briefing_and_wrap_use_the_quick_model_and_a_small_prompt(monkeypatch, tmp_path):
    m = Memory(tmp_path / "b.db")
    m.remember("Aneesh's son plays soccer on Saturdays")
    for write, label in ((proactive.morning_briefing, "briefing"), (proactive.evening_wrap, "evening_wrap")):
        seen = _capture(monkeypatch)
        asyncio.run(write(m))
        assert seen["label"] == label and seen["model"] == settings.fast_model
        assert len(seen["system"]) < 2500 and "soccer" in seen["system"]


def test_overdue_bill_says_overdue_and_how_to_stop_it():
    due = (local_now().date() - timedelta(days=5)).isoformat()
    text = money.bill_reminder_text({"biller": "Cloudflare", "amount": 32.0, "currency": "USD", "due_date": due})
    assert "was due" in text and "5 days ago" in text and "I paid Cloudflare" in text and "is due" not in text
    soon = (local_now().date() + timedelta(days=1)).isoformat()
    assert money.bill_reminder_text({"biller": "X", "amount": 1.0, "currency": "USD", "due_date": soon}).endswith(
        "is due tomorrow.")
