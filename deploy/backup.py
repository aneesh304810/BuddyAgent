"""Nightly backup: a consistent copy of the encrypted database, encrypted again
with your age public key. Only your age private key (kept off the server) can
open it, so a stolen backup is useless even together with the server.

Keeps the last 14 backups in /var/backups/buddy. If BUDDY_BACKUP_REMOTE is set
(an rclone remote such as "b2:my-bucket/buddy"), each backup is also copied there.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from buddy.db import connect  # noqa: E402

BACKUP_DIR = Path(os.getenv("BUDDY_BACKUP_DIR", "/var/backups/buddy"))
KEEP = 14


def main() -> None:
    recipient = os.environ["BUDDY_AGE_RECIPIENT"]
    source = os.environ["BUDDY_DB"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    snapshot = BACKUP_DIR / f".snapshot-{stamp}.db"
    target = BACKUP_DIR / f"buddy-{stamp}.db.age"

    src, dst = connect(source), connect(snapshot)  # same key: the snapshot stays encrypted
    src.backup(dst)
    dst.close()
    src.close()
    try:
        subprocess.run(["age", "-r", recipient, "-o", str(target), str(snapshot)], check=True)
    finally:
        snapshot.unlink(missing_ok=True)

    for old in sorted(BACKUP_DIR.glob("buddy-*.db.age"))[:-KEEP]:
        old.unlink()

    remote = os.getenv("BUDDY_BACKUP_REMOTE")
    if remote and shutil.which("rclone"):
        subprocess.run(["rclone", "copy", str(target), remote], check=True)
    print(f"backup written: {target}")


if __name__ == "__main__":
    main()
