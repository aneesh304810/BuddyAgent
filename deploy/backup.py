"""Nightly backup: a consistent copy of the encrypted database, encrypted again
with your age public key. Only your age private key (kept off the server) can
open it, so a stolen backup is useless even together with the server.

Each backup has a small companion, buddy-<stamp>.key.age: the database key, encrypted with the
same age public key. So the age private key alone is enough to restore everything, even if this
server is gone (deploy/restore.sh), while a stolen backup still opens for no one.

The library (uploaded documents, already encrypted on disk with a key derived from the database
key) goes in a third file, buddy-<stamp>.library.tar.age; only the last 3 are kept, as it can be big.

Keeps the last 14 backups in /var/backups/buddy. If BUDDY_BACKUP_REMOTE is set
(an rclone remote such as "b2:my-bucket/buddy"), each backup is also copied there.
"""

import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from buddy.db import connect  # noqa: E402

BACKUP_DIR = Path(os.getenv("BUDDY_BACKUP_DIR", "/var/backups/buddy"))
KEEP_LIBRARY = 3
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
    key_target = BACKUP_DIR / f"buddy-{stamp}.key.age"
    db_key = os.getenv("BUDDY_DB_KEY", "")
    if db_key:  # through stdin: the key never touches the disk unencrypted
        subprocess.run(["age", "-r", recipient, "-o", str(key_target)], input=f"BUDDY_DB_KEY={db_key}\n".encode(),
                       check=True)

    library_target = BACKUP_DIR / f"buddy-{stamp}.library.tar.age"
    library_dir = Path(os.getenv("BUDDY_LIBRARY_DIR", str(Path(source).parent / "library")))
    if library_dir.is_dir() and any(library_dir.iterdir()):
        archive = BACKUP_DIR / f".library-{stamp}.tar"
        try:
            with tarfile.open(archive, "w") as tar:
                tar.add(library_dir, arcname="library")
            subprocess.run(["age", "-r", recipient, "-o", str(library_target), str(archive)], check=True)
        finally:
            archive.unlink(missing_ok=True)

    for pattern, keep in (("buddy-*.db.age", KEEP), ("buddy-*.key.age", KEEP), ("buddy-*.library.tar.age", KEEP_LIBRARY)):
        for old in sorted(BACKUP_DIR.glob(pattern))[:-keep]:
            old.unlink()

    remote = os.getenv("BUDDY_BACKUP_REMOTE")
    if remote and shutil.which("rclone"):
        for path in (target, key_target, library_target):
            if path.exists():
                subprocess.run(["rclone", "copy", str(path), remote], check=True)
    print(f"backup written: {target}" + (f" (+ {key_target.name})" if key_target.exists() else ""))


if __name__ == "__main__":
    main()
