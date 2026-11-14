"""The price book (DuckDB), the pattern learner, the nightly sandbox job, merging months and 90-day retention."""

import asyncio
import base64
import gzip
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from buddy import patterns, pricebook, whentobuy
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    yield m
    pricebook.close_all()


def test_observe_history_stats_and_file_next_to_buddy(memory, tmp_path):
    pricebook.observe(memory, "product", "Sony WH-1000XM6", 399.0, seller="Best Buy", source="daily check")
    pricebook.observe(memory, "flight", "MIA-LIM", 412.0, travel_date="2027-03-10")
    pricebook.observe(memory, "hotel", "hotels:lima", 120.0, travel_date="2027-03-10", nights=3)
    pricebook.observe(memory, "car", "x", 1.0)  # unknown kinds are ignored
    assert pricebook.path_for(memory) == str(tmp_path / "pricebook.duckdb")
    assert [r["price"] for r in pricebook.history(memory, "sony wh-1000xm6")] == [399.0]
    assert pricebook.lowest(memory, "MIA-LIM", "flight") == 412.0
    s = pricebook.stats(memory)
    assert {r["domain"] for r in s["observations"]} == {"product", "flight", "hotel"}
    files = pricebook.export(memory, str(tmp_path / "out"))
    assert all(Path(f).exists() for f in files) and len(files) == 4
    pricebook.snapshot(memory, str(tmp_path / "pricebook-backup"))
    assert any((tmp_path / "pricebook-backup").iterdir())


def test_older_history_is_copied_in_once(tmp_path):
    m = Memory(tmp_path / "old.db")
    m.db.executescript("""
        CREATE TABLE price_seen (id INTEGER PRIMARY KEY, what TEXT, price REAL, currency TEXT, seller TEXT, url TEXT,
                                 seen_at TEXT);
        INSERT INTO price_seen (what, price, currency, seller, url, seen_at)
            VALUES ('kindle paperwhite', 139.0, 'USD', 'Amazon', '', '2026-11-01T10:00:00+00:00');
        CREATE TABLE travel_prices (route TEXT, day TEXT, source TEXT, price REAL, currency TEXT, seen_at TEXT);
        INSERT INTO travel_prices VALUES ('MIA-LIM', '2027-03-10', 'google', 400.0, 'USD', '2026-11-02T10:00:00+00:00');
        INSERT INTO travel_prices VALUES ('hotels:lima', '2027-03-10', 'serp', 99.0, 'USD', '2026-11-02T10:00:00+00:00');
    """)
    assert pricebook.lowest(m, "kindle paperwhite") == 139.0
    assert pricebook.lowest(m, "mia-lim", "flight") == 400.0 and pricebook.lowest(m, "hotels:lima", "hotel") == 99.0
    pricebook.close_all()
    pricebook.conn(m)  # reopened: not copied twice
    assert len(pricebook.history(m, "kindle paperwhite")) == 1
    pricebook.close_all()


def write_inputs(folder: Path):
    """Synthetic market data: headphones that drop 25% over Black Friday 2026, a flight route, predictions."""
    folder.mkdir(parents=True)
    rows = ["seen_at,domain,item,price,seller,travel_date"]
    for i in range(1, 6):
        item = f"headphones model {i}"
        for d in range(60):
            day = date(2026, 10, 20) + timedelta(days=d)
            bf = date(2026, 11, 27) <= day <= date(2026, 11, 30)
            rows.append(f"{day}T12:00:00,product,{item},{(150 if bf else 200) + i},Shop,")
    for d in range(30):
        seen = date(2026, 11, 1) + timedelta(days=d)
        rows.append(f"{seen}T09:00:00,flight,mia-lim,{300 + (30 - d) * 2},,2026-12-20")
    (folder / "observations.csv.gz").write_bytes(gzip.compress("\n".join(rows).encode()))
    items = "item,kind\n" + "\n".join(f"headphones model {i},electronics" for i in range(1, 6))
    (folder / "items.csv.gz").write_bytes(gzip.compress(items.encode()))
    launches = "item,kind,launch_date,launch_price,successor_date\n" + "\n".join(
        f"headphones model {i},electronics,2026-09-01,250," for i in range(1, 6))
    (folder / "launches.csv.gz").write_bytes(gzip.compress(launches.encode()))
    preds = "item,kind,decision,chance,outcome\nx,electronics,wait,0.6,right\ny,electronics,wait,0.6,wrong\n"
    (folder / "predictions.csv.gz").write_bytes(gzip.compress(preds.encode()))
    (folder / "events.json").write_text(json.dumps([{"name": "Black Friday to Cyber Monday", "start": "2026-11-27",
                                                      "end": "2026-11-30", "for": ["electronics"]}]))


def test_learner_finds_the_real_patterns(tmp_path):
    from buddy.sandbox_jobs import price_patterns
    write_inputs(tmp_path / "input")
    got = price_patterns.main(str(tmp_path))
    bf = got["event_discounts"]["electronics"]["Black Friday to Cyber Monday|2026"]
    assert bf["items"] == 5 and 0.24 <= bf["median"] <= 0.26 and bf["year"] == 2026
    assert "electronics" in got["lifecycle"] and got["flights"]["mia-lim"]["cheapest_window"] == "8-21"
    assert got["calibration"]["wait"] == {"right": 0.5, "odds_given": 0.6, "calls": 2}
    assert (tmp_path / "output" / "patterns.json").exists()


def fake_sandbox(root: Path):
    """The sandbox's ops, run locally in a folder (the real one runs them in a throwaway container)."""
    async def call(op, **kw):
        ws = root / kw.get("workspace", "")
        if op == "create":
            ws.mkdir(parents=True, exist_ok=True)
            return {"ok": True}
        if op == "run":
            assert kw.get("network") is False  # the learning never touches the network
            cmd = kw["command"].replace("python3", sys.executable)
            out = subprocess.run(cmd, shell=True, cwd=ws, capture_output=True, text=True)
            return {"output": out.stdout + out.stderr, "exit": out.returncode}
        if op == "write_b64":
            (ws / kw["path"]).parent.mkdir(parents=True, exist_ok=True)
            (ws / kw["path"]).write_bytes(base64.b64decode(kw["content_b64"]))
            return {"ok": True}
        if op == "write":
            (ws / kw["path"]).write_text(kw["content"])
            return {"ok": True}
        if op == "fetch":
            return {"base64": base64.b64encode((ws / "output" / kw["path"]).read_bytes()).decode()}
        raise AssertionError(op)
    return call


def test_nightly_learning_in_the_sandbox_and_buy_or_wait_uses_it(memory, tmp_path):
    seen = datetime(2026, 11, 1, tzinfo=timezone.utc)
    for i in range(1, 7):
        for d in range(45):
            day = seen + timedelta(days=d)
            bf = date(2026, 11, 27) <= day.date() <= date(2026, 11, 30)
            pricebook.observe(memory, "product", f"wireless headphones {i}", (140 if bf else 200) + i, seen_at=day)
    status = asyncio.run(patterns.learn(memory, call=fake_sandbox(tmp_path / "sandbox")))
    assert status.startswith("learned from 270 prices"), status
    got = patterns.get(memory)
    assert got["event_discounts"]["electronics"]["Black Friday to Cyber Monday"]["items"] == 6
    advice = whentobuy.advise(memory, "Bose QC Ultra headphones", 429.0, "electronics", today=date(2027, 11, 10),
                              save=False)
    assert any("learned from 6 items in your price book (2026)" in r for r in advice["reasons"])


def test_months_merge_and_sales_survive_raw_deletion():
    june = {"fitted_at": "2026-12-15", "prices": 100, "products": 5,
            "event_discounts": {"electronics": {"Black Friday to Cyber Monday|2026": {
                "median": 0.25, "p25": 0.2, "p75": 0.3, "items": 5, "event": "Black Friday to Cyber Monday",
                "year": 2026}}},
            "drop_odds": {"electronics": {"30": {"chance": 0.4, "items": 5}}}}
    later = {"fitted_at": "2027-12-10", "prices": 80, "products": 4,
             "event_discounts": {"electronics": {"Black Friday to Cyber Monday|2027": {
                 "median": 0.15, "p25": 0.1, "p75": 0.2, "items": 5, "event": "Black Friday to Cyber Monday",
                 "year": 2027}}},
             "drop_odds": {"electronics": {"30": {"chance": 0.2, "items": 15}}}}
    middle = {"fitted_at": "2027-03-01", "prices": 50, "products": 3}  # nothing about sales: raw data long gone
    m = patterns.merge([june, middle, later])
    bf = m["event_discounts"]["electronics"]["Black Friday to Cyber Monday"]
    assert bf["median"] == 0.2 and bf["items"] == 10 and bf["years"] == [2026, 2027]
    assert m["drop_odds"]["electronics"]["30"] == {"chance": 0.25, "items": 20}


def test_raw_prices_over_90_days_go_only_once_learned(memory):
    old = datetime.now(timezone.utc) - timedelta(days=120)
    pricebook.observe(memory, "product", "old thing", 10.0, seen_at=old)
    pricebook.observe(memory, "product", "new thing", 10.0)
    assert patterns.prune_raw(memory) == 0  # never learned: nothing deleted
    memory.set(patterns.LEARNED_KEY, date.today().isoformat())
    patterns.keep(memory, {"fitted_at": "now", "prices": 2})
    assert patterns.prune_raw(memory) == 1
    assert pricebook.lowest(memory, "old thing") is None and pricebook.lowest(memory, "new thing") == 10.0


def test_learning_waits_without_the_sandbox(memory, monkeypatch):
    from buddy import sandbox
    monkeypatch.setattr(sandbox, "available", lambda path=None: False)
    assert asyncio.run(patterns.learn(memory)).startswith("waiting: the sandbox isn't set up")
