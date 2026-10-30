#!/usr/bin/env bash
# Connect your banks and cards to Buddy through SimpleFIN Bridge (read-only):
#   sudo bash /opt/buddy/deploy/bank.sh
# Paste the setup token from bridge.simplefin.org when asked. It's swapped for a
# read-only access URL, saved in /etc/buddy/buddy.env, and Buddy restarts.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open
read -rsp "SimpleFIN setup token (hidden; paste, then Enter): " TOKEN; echo
TOKEN="$(echo -n "$TOKEN" | tr -d '[:space:]')"
CLAIM_URL="$(echo -n "$TOKEN" | base64 -d 2>/dev/null || true)"
[[ "$CLAIM_URL" == https://* ]] || { echo "That isn't a SimpleFIN setup token. Copy it again from the Bridge site."; exit 1; }
# Claiming works once: the token is used up and the access URL is returned.
ACCESS_URL="$(curl -fsS -X POST -H 'Content-Length: 0' "$CLAIM_URL")" || {
  echo "The Bridge refused the token (already used, or expired). Make a new one on bridge.simplefin.org."; exit 1; }
[[ "$ACCESS_URL" == https://* ]] || { echo "Unexpected answer from the Bridge."; exit 1; }
ACCESS_URL="$ACCESS_URL" python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path, line = sys.argv[1], "BUDDY_SIMPLEFIN_URL=" + os.environ["ACCESS_URL"].strip()
text = open(path).read()
if re.search(r"(?m)^BUDDY_SIMPLEFIN_URL=", text):
    text = re.sub(r"(?m)^BUDDY_SIMPLEFIN_URL=.*$", lambda m: line, text)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY
secrets_seal
systemctl restart buddy
echo "Connected. Buddy fetches your last 30 days now, then every 6 hours."
echo "Ask it: \"What are my account balances?\" or \"How much did I spend this month?\""
