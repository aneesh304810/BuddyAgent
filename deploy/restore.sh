#!/usr/bin/env bash
# Put a backup back, on this server or a new one (run bootstrap.sh there first):
#   sudo bash /opt/buddy/deploy/restore.sh                        the newest backup in /var/backups/buddy
#   sudo bash /opt/buddy/deploy/restore.sh /path/buddy-20261101-0315.db.age
# It asks for your backup key (AGE-SECRET-KEY-..., hidden while you paste). The database key comes
# from the backup's buddy-<stamp>.key.age; for an older backup without one, it asks for that too.
# The current database is kept as buddy.db.before-restore, and Buddy is restarted on the restored one.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
HERE="$(dirname "$(readlink -f "$0")")"
source "$HERE/secrets-lib.sh"
command -v age >/dev/null || { echo "age isn't installed: sudo apt install age"; exit 1; }

BACKUP="${1:-$(ls -1 /var/backups/buddy/buddy-*.db.age 2>/dev/null | sort | tail -n 1)}"
[[ -n $BACKUP && -f $BACKUP ]] || { echo "No backup found. Give its path: restore.sh /path/buddy-....db.age"; exit 1; }
KEYBACKUP="${BACKUP%.db.age}.key.age"
echo "Restoring $(basename "$BACKUP")"

WORK=$(mktemp -d /run/buddy-restore.XXXXXX)   # memory, root only
chmod 700 "$WORK"
cleanup() { shred -u "$WORK"/* 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT

read -rsp "Your backup key (AGE-SECRET-KEY-..., hidden): " AGE_KEY; echo
[[ $AGE_KEY == AGE-SECRET-KEY-* ]] || { echo "That isn't an age key (it starts AGE-SECRET-KEY-)."; exit 1; }
printf '%s\n' "$AGE_KEY" > "$WORK/identity"
unset AGE_KEY

if [[ -f $KEYBACKUP ]]; then
  DB_KEY=$(age -d -i "$WORK/identity" "$KEYBACKUP" | sed -n 's/^BUDDY_DB_KEY=//p')
else
  echo "This backup has no key file (it's from before they were added)."
  read -rsp "Your database key (BUDDY_DB_KEY, 64 characters, hidden): " DB_KEY; echo
  DB_KEY=${DB_KEY#BUDDY_DB_KEY=}
fi
[[ $DB_KEY =~ ^[0-9a-fA-F]{64}$ ]] || { echo "The database key should be 64 hex characters."; exit 1; }

age -d -i "$WORK/identity" -o "$WORK/buddy.db" "$BACKUP"
# Check the key really opens it before touching anything.
BUDDY_DB_KEY="$DB_KEY" /opt/buddy/backend/.venv/bin/python - "$WORK/buddy.db" <<'PY'
import sys
sys.path.insert(0, "/opt/buddy/backend")
from buddy.db import connect
tables = connect(sys.argv[1]).execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
print(f"The backup opens: {tables} tables.")
PY

secrets_open
trap 'secrets_close; cleanup' EXIT   # secrets_open set its own trap: keep both
TARGET=$(secrets_get BUDDY_DB)
TARGET=${TARGET:-/var/lib/buddy/buddy.db}
systemctl stop buddy
[[ -f $TARGET ]] && mv "$TARGET" "$TARGET.before-restore"
install -o buddy -g buddy -m 600 "$WORK/buddy.db" "$TARGET"
if [[ $(secrets_get BUDDY_DB_KEY) != "$DB_KEY" ]]; then
  secrets_set BUDDY_DB_KEY "$DB_KEY"
  echo "Set this server's database key to the backup's."
fi
secrets_seal
systemctl start buddy
echo "Restored. The previous database is at $TARGET.before-restore (delete it once all looks right)."
