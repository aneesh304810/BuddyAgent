#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server for Buddy.
#
#   curl -fsSL <raw URL of this file> -o bootstrap.sh   (or scp it)
#   sudo bash bootstrap.sh <git-repo-url> [age-public-key | generate] [branch]
#
# Phone-friendly: with "generate" (or no key) it creates the backup key and shows
# the secret half once for you to save; it asks for your Claude key and name
# instead of making you edit files.
#
# What it does, in order:
#   1. security updates on, automatic from now on
#   2. a locked-down "buddy" service user
#   3. Tailscale (you log in once via the link it prints)
#   4. firewall: nothing reachable from the internet, only over Tailscale
#   5. Buddy installed to /opt/buddy, secrets generated, service started
#   6. Buddy published on your tailnet at https://<host>.<tailnet>.ts.net
#   7. nightly encrypted backups
set -euo pipefail
trap 'echo "!! Setup stopped at line $LINENO ($BASH_COMMAND). It is safe to run the same command again." >&2' ERR

REPO_URL="${1:?usage: bootstrap.sh <git-repo-url> [age-public-key | generate] [branch]}"
AGE_RECIPIENT="${2:-generate}"
BRANCH="${3:-}"   # optional; default: the repo's default branch
APP=/opt/buddy
ENV_FILE=/etc/buddy/buddy.env

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
[[ "$AGE_RECIPIENT" == age1* || "$AGE_RECIPIENT" == generate ]] || {
  echo "The second argument must be an age public key (age1...) or the word: generate"; exit 1; }

echo "==> 1/7 Updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y python3 python3-venv git ufw unattended-upgrades age curl

if [[ "$AGE_RECIPIENT" == generate ]]; then
  if [[ -f $ENV_FILE ]] && grep -q "^BUDDY_AGE_RECIPIENT=age1" "$ENV_FILE"; then
    AGE_RECIPIENT=$(grep "^BUDDY_AGE_RECIPIENT=" "$ENV_FILE" | cut -d= -f2)   # re-run: keep the key
  else
    KEYDIR=$(mktemp -d)
    KEYFILE="$KEYDIR/backup.key"   # age-keygen refuses to write over an existing file
    age-keygen -o "$KEYFILE"
    AGE_RECIPIENT=$(age-keygen -y "$KEYFILE")
    echo
    echo "================ YOUR BACKUP KEY: SAVE IT NOW ================"
    echo "Copy the line below into your password manager (Samsung Pass,"
    echo "Bitwarden, Google Password Manager...). It is the ONLY way to open"
    echo "your backups, and it is deleted from this server in a moment."
    echo
    grep '^AGE-SECRET-KEY-' "$KEYFILE"
    echo
    echo "==============================================================="
    if [[ -t 0 ]]; then
      read -rp "Type SAVED once it's in your password manager: " answer
      until [[ "$answer" == SAVED ]]; do read -rp "Type SAVED: " answer; done
    fi
    shred -u "$KEYFILE" 2>/dev/null || rm -f "$KEYFILE"
    rmdir "$KEYDIR"
  fi
fi
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> 2/7 Service user"
id buddy >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/buddy --shell /usr/sbin/nologin buddy
install -d -o buddy -g buddy -m 700 /var/lib/buddy /var/backups/buddy
install -d -o root -g buddy -m 750 /etc/buddy

echo "==> 3/7 Tailscale"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! tailscale status >/dev/null 2>&1; then
  echo "Open the link below on your phone or laptop and log in to Tailscale:"
  tailscale up --ssh
fi
tailscale status >/dev/null || { echo "Tailscale is not connected; fix that and re-run."; exit 1; }

echo "==> 4/7 Firewall"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0
ufw allow 41641/udp   # Tailscale's direct connections (encrypted WireGuard)
ufw --force enable
echo "    Public SSH is now closed. Use: tailscale ssh root@$(hostname)"
echo "    (Your hosting provider's web console still works for emergencies.)"

echo "==> 5/7 Buddy"
if [[ -d $APP/.git ]]; then
  git -c safe.directory="$APP" -C "$APP" pull --ff-only   # owned by buddy, pulled by root
else
  git clone ${BRANCH:+--branch "$BRANCH"} "$REPO_URL" "$APP"
fi
chown -R buddy:buddy "$APP"
sudo -u buddy python3 -m venv "$APP/backend/.venv"
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q --upgrade pip setuptools
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q -r "$APP/backend/requirements.txt"
[[ -f $APP/backend/agents.yaml ]] || sudo -u buddy cp "$APP/backend/agents.example.yaml" "$APP/backend/agents.yaml"

if [[ ! -f $ENV_FILE ]]; then
  cat > "$ENV_FILE" <<ENV
# Buddy secrets. Readable only by root and the buddy service.
ANTHROPIC_API_KEY=REPLACE_ME
BUDDY_TOKEN=$(openssl rand -hex 24)
BUDDY_DB_KEY=$(openssl rand -hex 32)
BUDDY_DB=/var/lib/buddy/buddy.db
BUDDY_AGENTS=$APP/backend/agents.yaml
BUDDY_USER_NAME=friend
BUDDY_TIMEZONE=America/New_York
BUDDY_KNOWN_LANGUAGES=English, Hindi, Malayalam, Marathi, Tamil
BUDDY_MORNING_BRIEF=08:00
BUDDY_MONTHLY_BUDGET_USD=40
BUDDY_AGE_RECIPIENT=$AGE_RECIPIENT
ENV
  # Only root reads it: systemd passes the settings to Buddy, and agents (which run as
  # the buddy user) can't read the other secrets in it.
  chown root:root "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

install -m 644 "$APP/deploy/buddy.service" /etc/systemd/system/buddy.service
install -m 644 "$APP/deploy/buddy-backup.service" /etc/systemd/system/buddy-backup.service
install -m 644 "$APP/deploy/buddy-backup.timer" /etc/systemd/system/buddy-backup.timer
systemctl daemon-reload
systemctl enable buddy.service buddy-backup.timer
systemctl start buddy-backup.timer

echo "==> 6/7 Private HTTPS on your tailnet"
tailscale serve --bg --https=443 http://127.0.0.1:8000

echo "==> 7/7 Done"
if grep -q "ANTHROPIC_API_KEY=REPLACE_ME" "$ENV_FILE" && [[ -t 0 ]]; then
  echo
  read -rsp "Paste your Claude API key (starts with sk-ant-; hidden as you paste): " api_key; echo
  until [[ "$api_key" == sk-ant-* ]]; do
    read -rsp "That doesn't look like a Claude API key (sk-ant-...). Paste it again: " api_key; echo
  done
  read -rp "Your first name (what Buddy calls you): " first_name
  read -rp "Monthly AI budget in dollars [40]: " budget
  python3 - "$ENV_FILE" "$api_key" "${first_name:-friend}" "${budget:-40}" <<'PY'
import sys
path, key, name, budget = sys.argv[1:]
text = open(path).read()
text = text.replace("ANTHROPIC_API_KEY=REPLACE_ME", f"ANTHROPIC_API_KEY={key.strip()}")
text = text.replace("BUDDY_USER_NAME=friend", f"BUDDY_USER_NAME={name.strip()}")
text = text.replace("BUDDY_MONTHLY_BUDGET_USD=40", f"BUDDY_MONTHLY_BUDGET_USD={budget.strip()}")
open(path, "w").write(text)
PY
fi
if grep -q "ANTHROPIC_API_KEY=REPLACE_ME" "$ENV_FILE"; then
  echo "    Next: put your Claude API key and your name in $ENV_FILE, then:"
  echo "          systemctl restart buddy"
else
  systemctl restart buddy
fi
echo "    Open on your phone (with Tailscale on):"
echo "      https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')/?token=$(grep ^BUDDY_TOKEN= "$ENV_FILE" | cut -d= -f2)"
