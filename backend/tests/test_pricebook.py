"""The price book (DuckDB), the pattern learner, the nightly sandbox job, merging months and one-year retention."""

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


def test_raw_prices_over_a_year_go_only_once_learned(memory):
    old = datetime.now(timezone.utc) - timedelta(days=400)
    pricebook.observe(memory, "product", "last spring", 10.0, seen_at=datetime.now(timezone.utc) - timedelta(days=120))
    pricebook.observe(memory, "product", "old thing", 10.0, seen_at=old)
    pricebook.observe(memory, "product", "new thing", 10.0)
    assert patterns.prune_raw(memory) == 0  # never learned: nothing deleted
    memory.set(patterns.LEARNED_KEY, date.today().isoformat())
    patterns.keep(memory, {"fitted_at": "now", "prices": 2})
    assert patterns.prune_raw(memory) == 1
    assert pricebook.lowest(memory, "old thing") is None and pricebook.lowest(memory, "new thing") == 10.0
    assert pricebook.lowest(memory, "last spring") == 10.0  # four months old: kept now (a year, not 90 days)


def test_learning_waits_without_the_sandbox(memory, monkeypatch):
    from buddy import sandbox
    monkeypatch.setattr(sandbox, "available", lambda path=None: False)
    assert asyncio.run(patterns.learn(memory)).startswith("waiting: the sandbox isn't set up")


def test_the_price_book_opens_itself_again_after_the_database_engine_stops(tmp_path):
    from buddy.memory import Memory
    m = Memory(tmp_path / "b.db")
    pricebook.observe(m, "product", "Sony WH-1000XM6 headphones", 399.0, seller="Best Buy")
    where = pricebook.path_for(m)
    closed = []

    class Stopped:  # what every statement gets after the engine's internal error
        def cursor(self):
            raise RuntimeError('FATAL Error: Failed: database has been invalidated because of a previous fatal error. '
                               'The database must be restarted prior to being used again.')

        def close(self):
            closed.append(True)

    pricebook._open.pop(where).close()
    pricebook._open[where] = Stopped()
    rows = pricebook.run(m, "SELECT COUNT(*) AS n FROM observations")     # opened again, and answered
    assert rows == [{"n": 1}] and closed == [True] and not isinstance(pricebook._open[where], Stopped)
    with pytest.raises(Exception, match="no_such_table"):                 # an ordinary mistake is still an error
        pricebook.run(m, "SELECT * FROM no_such_table")
    pricebook.close_all()


def test_the_product_index_is_removed_and_a_failed_filing_does_not_take_the_pages_down(tmp_path, monkeypatch):
    import duckdb

    from buddy.memory import Memory
    m = Memory(tmp_path / "b.db")
    where = pricebook.path_for(m)
    pricebook.observe(m, "product", "LG 27GP850 monitor", 249.0, seller="Newegg")
    pricebook.close_all()
    c = duckdb.connect(where)                                             # the index an earlier version made
    c.execute("CREATE INDEX obs_product ON observations (domain, product)")
    c.close()
    names = {r["index_name"] for r in pricebook.run(m, "SELECT index_name FROM duckdb_indexes()")}
    assert names == set()                                                 # no secondary index is kept

    pricebook.run(m, "INSERT INTO deals (seen_on, source, title, url, price, store) VALUES (current_date, 'feed', "
                     "'LG 27GP850 Monitor $249', 'https://x.example/1', 249, 'Newegg')")

    def broken(*a, **k):
        raise RuntimeError("INTERNAL Error: Attempted to dereference shared_ptr that is NULL!")

    monkeypatch.setattr(pricebook, "_identity", broken)
    assert pricebook.identify(m) == 0                                     # it failed, and said so only in the log
    monkeypatch.setattr(pricebook, "_identity", lambda *a, **k: pytest.fail("filing was tried again"))
    page = pricebook.deals_page(m, 7)                                     # the page still lists the deal, unfiled
    assert page["counts"]["product"] == 1 and page["deals"][0]["title"].startswith("LG 27GP850")
    assert pricebook.history(m, "LG 27GP850 monitor")                     # and its price history is still readable
    pricebook.close_all()


def _internal(*a, **k):
    raise RuntimeError("INTERNAL Error: Attempted to dereference shared_ptr that is NULL!")


def test_an_internal_error_on_a_statement_reopens_the_price_book_and_asks_again(tmp_path):
    from buddy.memory import Memory
    m = Memory(tmp_path / "b.db")
    pricebook.observe(m, "product", "Monster open ear headphones", 19.49, seller="Amazon")
    where = pricebook.path_for(m)
    real = pricebook._open.pop(where)
    real.close()

    class FailsOnce:  # the engine's internal error on this very statement, not on an earlier one
        def cursor(self):
            _internal()

        def close(self):
            pass

    pricebook._open[where] = FailsOnce()
    assert pricebook.chart(m, "Monster open ear headphones")["lowest"] == 19.49  # opened again, and answered
    assert not (tmp_path / "pricebook.duckdb.before-rebuild").exists()            # once was enough: no rebuild
    pricebook.close_all()


def test_the_same_internal_error_twice_rebuilds_the_file_once_and_keeps_the_old_one(tmp_path, monkeypatch):
    from buddy.memory import Memory
    m = Memory(tmp_path / "b.db")
    for day, price in ((3, 24.99), (2, 21.0), (1, 19.49)):
        pricebook.observe(m, "product", "Monster open ear headphones", price, seller="Amazon",
                          seen_at=datetime.now(timezone.utc) - timedelta(days=day))
    pricebook.observe(m, "flight", "MIA-SJU", 129.0, seller="JetBlue")
    pricebook.run(m, "INSERT INTO deals (seen_on, source, title, url, price, store) VALUES (current_date, 'feed', "
                     "'Monster Open Ear Headphones $19.49', 'https://x.example/1', 19.49, 'Amazon')")
    where = pricebook.path_for(m)
    opened, real_conn = [], pricebook.conn

    def conn(memory):  # the file trips the engine until it has been rebuilt
        c = real_conn(memory)
        if (tmp_path / "pricebook.duckdb.before-rebuild").exists():
            return c
        opened.append(True)

        class Trips:
            cursor = staticmethod(_internal)
        return Trips()

    monkeypatch.setattr(pricebook, "conn", conn)
    got = pricebook.chart(m, "Monster open ear headphones")
    assert got["lowest"] == 19.49 and len(got["points"]) == 3                    # answered, after the rebuild
    assert len(opened) == 2                                                       # asked, opened again, then rebuilt
    assert (tmp_path / "pricebook.duckdb.before-rebuild").exists() and not (tmp_path / "pricebook.duckdb.export").exists()
    monkeypatch.setattr(pricebook, "conn", real_conn)
    assert pricebook.run(m, "SELECT COUNT(*) AS n FROM observations") == [{"n": 4}]
    assert pricebook.run(m, "SELECT COUNT(*) AS n FROM deals") == [{"n": 1}]
    pricebook.observe(m, "product", "Monster open ear headphones", 18.0, seller="Amazon")   # numbering carries on
    ids = [r["id"] for r in pricebook.run(m, "SELECT id FROM observations ORDER BY id")]
    assert len(set(ids)) == 5 and where in pricebook._rebuilt
    pricebook.close_all()


def test_a_file_that_still_fails_after_the_rebuild_says_so_and_is_not_rebuilt_again(tmp_path, monkeypatch):
    from buddy.memory import Memory
    m = Memory(tmp_path / "b.db")
    pricebook.observe(m, "product", "Sony WH-1000XM6 headphones", 399.0, seller="Best Buy")
    rebuilds = []
    real_rebuild = pricebook.rebuild
    monkeypatch.setattr(pricebook, "rebuild", lambda memory: rebuilds.append(1) or real_rebuild(memory))

    class Trips:
        cursor = staticmethod(_internal)

        def close(self):
            pass

    monkeypatch.setattr(pricebook, "conn", lambda memory: Trips())
    for _ in range(2):
        with pytest.raises(RuntimeError, match="INTERNAL Error"):
            pricebook.run(m, "SELECT 1")
    assert rebuilds == [1]
    monkeypatch.undo()
    pricebook.close_all()


def test_check_reads_every_table_and_the_pages_statements_and_changes_nothing(tmp_path):
    from buddy.memory import Memory
    m = Memory(tmp_path / "b.db")
    pricebook.observe(m, "product", "Sony WH-1000XM6 headphones", 399.0, seller="Best Buy")
    got = pricebook.check(m)
    assert got["ok"] and got["tables"]["observations"] == 1 and got["tables"]["deals"] == 0
    assert got["queries"]["price history"] == "ok" and got["queries"]["indexes"] == "none"
    text = pricebook.check_text(m)
    assert "It can be read." in text and "table observations: 1 rows" in text and "database engine" in text
    assert not (tmp_path / "pricebook.duckdb.before-rebuild").exists()
    pricebook.close_all()
