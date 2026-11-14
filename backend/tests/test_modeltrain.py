"""Model training: waits for enough data, trains in the sandbox, is promoted only on unseen calls, and is used."""

import asyncio
import json
import random
from datetime import date, datetime, timedelta

import pytest

from buddy import modeltrain, pricebook, recommend, simbook, whentobuy
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory
from buddy.sandbox_jobs import train_model
from tests.test_pricebook import fake_sandbox

START = date(2026, 1, 1)


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def trades(memory, n, pattern=True, seed=7):
    """n closed paper buys. With a pattern: a positive tone and a calm RSI win much more often. Without: a coin."""
    rnd = random.Random(seed)
    simbook._ready(memory)
    for i in range(n):
        tone, rsi = rnd.choice([1, 1, 0, -1]), rnd.uniform(35, 85)
        p = (0.25 + 0.3 * (tone > 0) + 0.3 * (rsi < 60)) if pattern else 0.5
        won = rnd.random() < p
        pricebook.run(memory, "INSERT INTO sim_trades (opened, symbol, category, side, entry, shares, stop, target, "
                              "parts, read, status, closed, exit, why, ret, risk, features) VALUES (?, 'S', 'stocks', "
                              "'long', 100, 100, 93, 114, ?, 'fair', 'closed', ?, 100, 'x', ?, 700, ?)",
                      ((START + timedelta(days=i)).isoformat(), json.dumps({"tone": tone, "trend": 1}),
                       (START + timedelta(days=i + 5)).isoformat(), 0.1 if won else -0.07,
                       json.dumps({"rsi": round(rsi, 1), "tone": tone * 0.5, "mentions": rnd.randint(3, 12)})))


def run_week(memory, tmp_path):
    return asyncio.run(modeltrain.weekly(memory, call=fake_sandbox(tmp_path / "sandbox")))


def test_nothing_is_trained_until_there_is_enough(memory, tmp_path):
    trades(memory, 120)
    r = modeltrain.readiness(memory, "investing")
    assert r["rows"] == 120 and not r["enough"] and not r["train_now"] and r["needed"] == 200
    assert run_week(memory, tmp_path) == [] and modeltrain.active(memory, "investing") is None
    assert modeltrain.status(memory)["domains"]["investing signals"]["last_run"] is None


def test_a_real_pattern_is_learned_tested_on_unseen_trades_and_promoted(memory, tmp_path):
    trades(memory, 400)
    told = run_week(memory, tmp_path)
    assert len(told) == 1 and told[0].startswith("🧪 I had enough data to train a model on my investing signals. "
                                                 "It passed its test and is now in use.")
    assert "tested on the 120 newest it had never seen" in told[0] and "✅" in told[0] and "❌" not in told[0]
    m = modeltrain.active(memory, "investing")
    assert m["learned_from"] == 280 and m["tested_on"] == 120 and m["right_when_liked"] > m["right_overall"] + 0.05
    assert m["model"]["weights"]["rsi"] < 0 and m["model"]["weights"]["reason_tone"] > 0   # what the pattern was
    good = modeltrain.predict(memory, "investing", {"reason_tone": 1, "reason_trend": 1, "rsi": 45, "tone": 0.5,
                                                    "mentions": 6, "is_buy": 1})
    bad = modeltrain.predict(memory, "investing", {"reason_tone": -1, "reason_trend": 1, "rsi": 82, "tone": -0.5,
                                                   "mentions": 6, "is_buy": 1})
    assert good["p"] > 0.65 > 0.4 > bad["p"] and good["tested_on"] == 120
    assert run_week(memory, tmp_path) == []                       # no new data: not trained again
    trades(memory, 60, seed=9)
    assert len(run_week(memory, tmp_path)) == 1                   # 50 more judged trades: a retrain


def test_noise_is_rejected_and_an_old_model_is_retired(memory, tmp_path):
    trades(memory, 300, pattern=True)
    run_week(memory, tmp_path)
    assert modeltrain.active(memory, "investing") is not None
    pricebook.run(memory, "DELETE FROM sim_trades")
    trades(memory, 400, pattern=False, seed=3)                    # the pattern stopped holding
    told = run_week(memory, tmp_path)
    assert "It didn't pass its test, so I'm not using it." in told[0] and "❌" in told[0]
    assert "My usual weighted reasons stay in charge" in told[0]
    assert modeltrain.active(memory, "investing") is None and modeltrain.predict(memory, "investing", {}) is None
    last = modeltrain.status(memory)["domains"]["investing signals"]
    assert last["model_in_use"] is False and last["last_run"]["status"] == "rejected"


def test_shopping_calls_train_the_same_way_and_the_next_call_uses_the_model(memory, tmp_path):
    rnd = random.Random(5)
    whentobuy._ready(memory)
    for i in range(320):
        sale = rnd.random() < 0.5
        fell = rnd.random() < (0.8 if sale else 0.15)             # a sale ahead really did mean a lower price
        pricebook.run(memory, "INSERT INTO predictions (domain, item, kind, made_on, price, decision, chance, target, "
                              "event, by_date, parts, outcome, best_seen) VALUES ('product', 'x', 'electronics', ?, "
                              "100, 'wait', 0.5, 90, '', ?, ?, ?, ?)",
                      ((START + timedelta(days=i)).isoformat(), (START + timedelta(days=i + 20)).isoformat(),
                       json.dumps({"sale_ahead": 0.45} if sale else {"near_lowest": -0.2}),
                       "right" if fell else "wrong", 85.0 if fell else 99.0))
    told = run_week(memory, tmp_path)
    assert any("shopping calls" in t and "is now in use" in t for t in told)
    got = whentobuy.advise(memory, "Sony WH-1000XM6", 399.0, "electronics", today=date(2026, 11, 10), save=False)
    assert any(r.startswith("my trained model puts the chance of a lower price at") for r in got["reasons"])
    assert modeltrain.readiness(memory, "flight")["rows"] == 0


def test_a_passed_model_can_turn_a_buy_into_a_caution(memory, tmp_path, monkeypatch):
    from tests.test_recommend import NO_LENS, FakeData, attention, wavy
    attention(memory, "NVDA", 8, 0.6)
    monkeypatch.setattr(modeltrain, "predict", lambda memory, domain, x: {
        "p": 0.3, "tested_on": 120, "right_when_liked": 0.7, "right_overall": 0.5} if x.get("is_buy") else None)
    told = recommend.run(memory, date(2026, 11, 16), FakeData({"NVDA": wavy(True, 260), "SPY": wavy(True, 260)}),
                         lambda s: {"insiders": 30.0}, NO_LENS)
    assert told[0].startswith("⚠️ NVDA") and "30% chance of working" in told[0] and "doesn't back this buy" in told[0]


def test_the_trainer_itself_and_the_weekly_schedule(memory, tmp_path, monkeypatch):
    assert train_model.evaluate([{"when": "2026-01-01", "x": {"a": 1}, "y": 1}] * 30)["status"] == "not enough"
    assert modeltrain.due(memory, datetime(2026, 11, 16, 4, 0)) and not modeltrain.due(memory, datetime(2026, 11, 16, 1))
    memory.set(modeltrain.WEEK_KEY, "2026-47")
    assert not modeltrain.due(memory, datetime(2026, 11, 18, 4, 0))
    from buddy import sandbox
    monkeypatch.setattr(sandbox, "available", lambda path=None: False)
    trades(memory, 300)
    told = asyncio.run(modeltrain.weekly(memory))
    assert len(told) == 1 and "sandbox isn't set up" in told[0]
    assert asyncio.run(modeltrain.weekly(memory)) == []           # said once, not every week
