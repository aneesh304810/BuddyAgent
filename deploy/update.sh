#!/usr/bin/env bash
# Update Buddy to the newest code and restart it. Run on the server as root:
#   sudo bash /opt/buddy/deploy/update.sh
# The code is fetched as root (root holds the GitHub deploy key) and handed back
# to the buddy user, which runs it.
set -euo pipefail
APP=/opt/buddy
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
BEFORE=$(sha256sum "$APP/deploy/update.sh" | cut -d' ' -f1)
git -c safe.directory="$APP" -C "$APP" pull --ff-only
# If this script itself changed, run the new version (bash would keep running the old one).
if [ "${BUDDY_UPDATE_REEXEC:-}" != 1 ] && [ "$BEFORE" != "$(sha256sum "$APP/deploy/update.sh" | cut -d' ' -f1)" ]; then
  echo "  The update script changed: running the new version"
  BUDDY_UPDATE_REEXEC=1 exec bash "$APP/deploy/update.sh" "$@"
fi
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
# Graphviz draws Buddy's diagrams.
if ! command -v dot >/dev/null; then
  echo "  Installing Graphviz (for diagrams)"
  { apt-get update -q >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -q graphviz >/dev/null; } \
    || echo "  !! Graphviz didn't install; diagrams won't work until: sudo apt-get update && sudo apt-get install -y graphviz"
fi
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q --upgrade -r "$APP/backend/requirements.txt"
# The settings hold every secret: root only (systemd hands Buddy what it needs).
for f in /etc/buddy/buddy.env /etc/buddy/buddy.env.cred /etc/buddy/key-dates; do
  [ -f "$f" ] && chown root:root "$f" && chmod 600 "$f"
done
# Weekly patching and security check, auto-reboot for security updates, extra service lockdown.
bash "$APP/deploy/security.sh" install
HARDENED=0
if [ ! -f /etc/systemd/system/buddy.service.d/hardening.conf ] && [ ! -f /etc/buddy/no-hardening ]; then
  HARDENED=1
  bash "$APP/deploy/security.sh" harden-file
fi
systemctl daemon-reload
if systemctl list-unit-files buddy-sandbox.service >/dev/null 2>&1 && [ -f /etc/systemd/system/buddy-sandbox.service ]; then
  echo "  Updating the code sandbox"
  install -o root -g root -m 755 "$APP/deploy/sandbox/sandboxd.py" /usr/local/lib/buddy-sandbox/sandboxd.py
  docker build -q -t buddy-sandbox:latest "$APP/deploy/sandbox" >/dev/null && systemctl restart buddy-sandbox
fi
systemctl restart buddy
for _ in $(seq 30); do   # starting takes a few seconds (agents start too)
  if curl -fsS http://127.0.0.1:8000/health 2>/dev/null; then echo "  Buddy updated and running."; exit 0; fi
  sleep 1
done
if [ "$HARDENED" = 1 ]; then
  # The new lockdown may not suit something on this server: take it off and try again.
  rm -f /etc/systemd/system/buddy.service.d/hardening.conf && touch /etc/buddy/no-hardening
  systemctl daemon-reload && systemctl restart buddy
  for _ in $(seq 30); do
    if curl -fsS http://127.0.0.1:8000/health 2>/dev/null; then
      echo "  Buddy updated and running (without the extra lockdown, which it didn't start with; tell me the log above)."
      journalctl -u buddy -n 15 --no-pager; exit 0
    fi
    sleep 1
  done
fi
echo "!! Buddy didn't start. The last lines of its log:"
journalctl -u buddy -n 25 --no-pager
exit 1
