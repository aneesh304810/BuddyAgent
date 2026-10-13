import pytest

from buddy import db


def test_encrypted_db_is_unreadable_without_key(tmp_path, monkeypatch):
    path = tmp_path / "b.db"
    key = "ab" * 32
    monkeypatch.setenv("BUDDY_DB_KEY", key)
    conn = db.connect(path)
    conn.execute("CREATE TABLE t (secret TEXT)")
    conn.execute("INSERT INTO t VALUES ('HDFC bill 18400')")
    conn.commit()
    conn.close()
    assert b"HDFC" not in path.read_bytes()

    monkeypatch.setenv("BUDDY_DB_KEY", "cd" * 32)
    with pytest.raises(Exception):
        db.connect(path)

    monkeypatch.setenv("BUDDY_DB_KEY", key)
    assert db.connect(path).execute("SELECT secret FROM t").fetchone()["secret"] == "HDFC bill 18400"


def test_rejects_malformed_key(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDDY_DB_KEY", "short")
    with pytest.raises(ValueError):
        db.connect(tmp_path / "x.db")
