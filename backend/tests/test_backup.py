"""Nightly backup: the database plus its key, both encrypted with the owner's age key."""

import importlib.util
import sqlite3
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("backup", Path(__file__).resolve().parents[2] / "deploy" / "backup.py")


def test_backup_carries_the_database_key_encrypted(tmp_path, monkeypatch):
    backup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(backup)
    db = tmp_path / "buddy.db"
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
    out = tmp_path / "backups"
    out.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", out)
    monkeypatch.setattr(backup, "connect", lambda p: sqlite3.connect(p))
    monkeypatch.setenv("BUDDY_AGE_RECIPIENT", "age1test")
    monkeypatch.setenv("BUDDY_DB", str(db))
    monkeypatch.setenv("BUDDY_DB_KEY", "ab" * 32)
    monkeypatch.delenv("BUDDY_BACKUP_REMOTE", raising=False)
    calls = []

    def fake_run(args, input=None, check=False):  # stands in for age: "encrypts" by copying
        calls.append((args, input))
        target = Path(args[args.index("-o") + 1])
        target.write_bytes(b"AGE:" + (input if input is not None else Path(args[-1]).read_bytes()))
    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    backup.main()
    key_file = next(out.glob("buddy-*.key.age"))
    assert key_file.read_bytes() == b"AGE:BUDDY_DB_KEY=" + b"ab" * 32 + b"\n"
    assert all(a[:3] == ["age", "-r", "age1test"] for a, _ in calls)  # both through age, to the owner's key
    assert next(out.glob("buddy-*.db.age")).exists()
    assert not list(out.glob(".snapshot-*"))  # no unencrypted copy left behind
