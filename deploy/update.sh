#!/usr/bin/env bash
# Update Buddy to the newest code and restart it. Run on the server as root:
#   sudo bash /opt/buddy/deploy/update.sh
# The code is fetched as root (root holds the GitHub deploy key) and handed back
# to the buddy user, which runs it.
set -euo pipefail
APP=/opt/buddy
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
git -c safe.directory="$APP" -C "$APP" pull --ff-only
chown -R buddy:buddy "$APP"
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"
systemctl restart buddy
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo "  Buddy updated and running."
