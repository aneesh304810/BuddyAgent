"""The shared database connection under many threads at once: no API misuse, no lost writes."""

import threading

from buddy.db import Done, connect
from buddy.memory import Memory


def test_many_threads_reading_writing_and_committing_on_one_connection(tmp_path):
    memory = Memory(tmp_path / "buddy.db")
    memory.db.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER, who INTEGER)")
    errors, per_thread, threads = [], 150, 8

    def work(who):
        try:
            for n in range(per_thread):
                memory.db.execute("INSERT INTO t VALUES (?, ?)", (n, who))
                if n % 7 == 0:
                    memory.db.commit()
                for _ in memory.db.execute("SELECT n FROM t WHERE who = ? ORDER BY n DESC LIMIT 5", (who,)):
                    pass
                memory.set(f"k{who}", str(n))
                assert memory.get(f"k{who}") == str(n)
        except Exception as e:  # any driver error here is the bug this guards against
            errors.append(repr(e))

    pool = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for p in pool:
        p.start()
    for p in pool:
        p.join()
    memory.db.commit()
    assert errors == []
    assert memory.db.execute("SELECT COUNT(*) AS c FROM t").fetchone()["c"] == per_thread * threads


def test_a_finished_statement_answers_like_a_cursor(tmp_path):
    db = connect(tmp_path / "x.db")
    db.execute("CREATE TABLE t (a INTEGER)")
    made = db.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    assert isinstance(made, Done) and made.rowcount == 3
    assert db.execute("INSERT INTO t VALUES (4)").lastrowid == 4
    got = db.execute("SELECT a FROM t ORDER BY a")
    assert got.fetchone()["a"] == 1 and got.fetchmany(2)[1]["a"] == 3 and [r["a"] for r in got] == [4]
    assert got.fetchone() is None and got.fetchall() == [] and got.description[0][0] == "a"
    assert db.execute("UPDATE t SET a = a + 1 WHERE a > 2").rowcount == 2
    assert [r["a"] for r in db.execute("SELECT a FROM t ORDER BY a").fetchall()] == [1, 2, 4, 5]
    db.rollback()
