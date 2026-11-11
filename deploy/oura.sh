#!/usr/bin/env bash
# Link Buddy to your Oura Ring (Buddy Connect, Health group), once:
#   sudo bash /opt/buddy/deploy/oura.sh
# Before: an Oura API application (cloud.ouraring.com/oauth/applications, or developer.ouraring.com) with the
# redirect URI below. It asks for the app's client ID and secret (hidden), gives you Oura's sign-in link, and swaps
# the code you get back for a refresh token. All three are saved in Buddy's settings (encrypted if sealed); none is
# printed. Oura's refresh tokens work once: Buddy saves each new one itself from then on.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open

REDIRECT_DEFAULT="https://www.google.com"
read -rp "Oura app client ID: " CLIENT_ID
read -rsp "Oura app client secret (hidden): " CLIENT_SECRET; echo
read -rp "Redirect URI on the Oura app (Enter for $REDIRECT_DEFAULT): " REDIRECT
REDIRECT=${REDIRECT:-$REDIRECT_DEFAULT}
[[ -n $CLIENT_ID && -n $CLIENT_SECRET ]] || { echo "Both the client ID and secret are needed."; exit 1; }

export CLIENT_ID CLIENT_SECRET REDIRECT
URL=$(python3 - <<'PY'
import os, secrets, urllib.parse
q = {"response_type": "code", "client_id": os.environ["CLIENT_ID"], "redirect_uri": os.environ["REDIRECT"],
     "state": secrets.token_urlsafe(12)}
print("https://cloud.ouraring.com/oauth/authorize?" + urllib.parse.urlencode(q))
PY
)
echo
echo "1. Open this link on your phone or computer, sign in to Oura, and allow everything it lists"
echo "   (Buddy only reads):"
echo
echo "   $URL"
echo
echo "2. Oura then opens $REDIRECT with ?code=... in the address. Copy that whole address (or just the code)."
echo "   Do it within 10 minutes: the code expires."
read -rsp "3. Paste it here (hidden), then Enter: " PASTED; echo
export PASTED

RESULT=$(python3 - <<'PY'
import json, os, sys, urllib.parse, urllib.request
pasted = os.environ["PASTED"].strip()
code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [pasted])[0]
body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code,
                               "client_id": os.environ["CLIENT_ID"], "client_secret": os.environ["CLIENT_SECRET"],
                               "redirect_uri": os.environ["REDIRECT"]}).encode()
req = urllib.request.Request("https://api.ouraring.com/oauth/token", data=body,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        got = json.load(r)
except urllib.error.HTTPError as e:
    print("ERR " + e.read(300).decode("utf-8", "replace").replace("\n", " ")); sys.exit(0)
token = got.get("refresh_token", "")
print("OK " + token if token else "ERR Oura gave no refresh token")
PY
)
if [[ $RESULT != OK* ]]; then
  echo "Oura didn't accept it: ${RESULT#ERR }"
  echo "Check the redirect URI matches the Oura app exactly, then run this again (a code works once)."
  exit 1
fi
export REFRESH="${RESULT#OK }"

python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path = sys.argv[1]
values = {"BUDDY_OURA_CLIENT_ID": os.environ["CLIENT_ID"], "BUDDY_OURA_CLIENT_SECRET": os.environ["CLIENT_SECRET"],
          "BUDDY_OURA_REFRESH_TOKEN": os.environ["REFRESH"]}
text = open(path).read()
for name, value in values.items():
    line = f"{name}={value.strip()}"
    if re.search(rf"(?m)^{name}=", text):
        text = re.sub(rf"(?m)^{name}=.*$", lambda m: line, text)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
print("Saved the Oura link (3 settings).")
PY
unset CLIENT_SECRET REFRESH PASTED
secrets_seal
systemctl restart buddy
echo "Buddy restarted. In the app: Menu > Connections > Health > Oura Ring > Test. Then ask Buddy:"
echo "\"how did I sleep?\" or \"let's set up my health plan\"."
