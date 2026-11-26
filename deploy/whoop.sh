#!/usr/bin/env bash
# Make WHOOP available on this Buddy (Buddy Connect, Health group), once per server:
#   sudo bash /opt/buddy/deploy/whoop.sh
# It shows the redirect URI to put on your WHOOP app, then asks for the app's client ID and secret (hidden) and saves
# them in Buddy's settings (encrypted if sealed). After that, each person using Buddy connects their own WHOOP in the
# app: Menu > Connections > Health > WHOOP > Connect. Their tokens stay in Buddy's encrypted database.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open

HOST=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' || true)
[[ -n $HOST ]] || { echo "Couldn't read this server's Tailscale name (is Tailscale up?)."; exit 1; }
REDIRECT="https://$HOST/connect/whoop/linked"

echo "1. On developer.whoop.com (WHOOP Developer Dashboard), create an app"
echo "   (or edit yours) and set its Redirect URI to exactly:"
echo
echo "   $REDIRECT"
echo
echo "   It is this server's own page on your private Tailscale network: WHOOP sends the browser back there after"
echo "   sign-in, so no code passes through any other site. Tick the read scopes and offline (Buddy only reads)."
echo
read -rp "2. WHOOP app client ID: " CLIENT_ID
read -rsp "   WHOOP app client secret (hidden): " CLIENT_SECRET; echo
[[ -n $CLIENT_ID && -n $CLIENT_SECRET ]] || { echo "Both the client ID and secret are needed."; exit 1; }
export CLIENT_ID CLIENT_SECRET REDIRECT

python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path = sys.argv[1]
values = {"BUDDY_WHOOP_CLIENT_ID": os.environ["CLIENT_ID"], "BUDDY_WHOOP_CLIENT_SECRET": os.environ["CLIENT_SECRET"],
          "BUDDY_WHOOP_REDIRECT": os.environ["REDIRECT"]}
text = open(path).read()
for name, value in values.items():
    line = f"{name}={value.strip()}"
    if re.search(rf"(?m)^{name}=", text):
        text = re.sub(rf"(?m)^{name}=.*$", lambda m: line, text)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
print("Saved the WHOOP app (3 settings).")
PY
unset CLIENT_SECRET
secrets_seal
systemctl restart buddy
echo "Buddy restarted. Now, in the Buddy app: Menu > Connections > Health > WHOOP > Connect,"
echo "sign in to WHOOP and allow access."
