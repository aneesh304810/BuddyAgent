#!/usr/bin/env bash
# Update Buddy to the newest code and restart it. Run on the server as root:
#   sudo bash /opt/buddy/deploy/update.sh
# The code is fetched as root (root holds the GitHub deploy key) and handed back
# to the buddy user, which runs it.
set -euo pipefail
APP=/opt/buddy
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
git -c safe.directory="$APP" -C "$APP" pull --ff-only
# Settings files written by older versions: bring the gmail agent up to date (all accounts).
python3 - "$APP/backend/agents.yaml" <<'PY'
import re, sys
path = sys.argv[1]
try:
    text = open(path).read()
except FileNotFoundError:
    sys.exit()
new = re.sub(r"env_from: \[BUDDY_EMAIL_ADDRESS[^\]]*\]", 'env_from: ["BUDDY_EMAIL_*"]', text)
new = new.replace("unsubscribe_links, create_draft]", "unsubscribe_links, create_draft, list_accounts]")
if new != text:
    open(path, "w").write(new)
    print("Updated the gmail agent in agents.yaml (all email accounts).")
PY
chown -R buddy:buddy "$APP"
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q --upgrade pip setuptools
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q --upgrade -r "$APP/backend/requirements.txt"
# The settings file holds every secret: root only (systemd hands Buddy what it needs).
chown root:root /etc/buddy/buddy.env && chmod 600 /etc/buddy/buddy.env
systemctl restart buddy
for _ in $(seq 30); do   # starting takes a few seconds (agents start too)
  if curl -fsS http://127.0.0.1:8000/health 2>/dev/null; then echo "  Buddy updated and running."; exit 0; fi
  sleep 1
done
echo "!! Buddy didn't start. The last lines of its log:"
journalctl -u buddy -n 25 --no-pager
exit 1
