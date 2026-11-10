#!/usr/bin/env bash
# Link Buddy to your Nest thermostat (Buddy Connect, Home group), once:
#   sudo bash /opt/buddy/deploy/nest.sh
# Before: Device Access registration (console.nest.google.com/device-access), a Google Cloud project with the
# Smart Device Management API on, an OAuth client (Web application) with https://www.google.com as an authorized
# redirect URI, the consent screen "In production", and a Device Access project with that client ID.
# It asks for the project ID, client ID and secret (hidden), gives you Google's sign-in link, and swaps the code
# you get back for a long-lived refresh token. All four are saved in Buddy's settings (encrypted if sealed); none
# is printed.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open

read -rp "Device Access project ID (from console.nest.google.com/device-access): " PROJECT
read -rp "OAuth client ID (ends in .apps.googleusercontent.com): " CLIENT_ID
read -rsp "OAuth client secret (hidden): " CLIENT_SECRET; echo
[[ -n $PROJECT && -n $CLIENT_ID && -n $CLIENT_SECRET ]] || { echo "All three are needed."; exit 1; }

export PROJECT CLIENT_ID CLIENT_SECRET
URL=$(python3 - <<'PY'
import os, urllib.parse
q = {"redirect_uri": "https://www.google.com", "access_type": "offline", "prompt": "consent",
     "client_id": os.environ["CLIENT_ID"], "response_type": "code",
     "scope": "https://www.googleapis.com/auth/sdm.service"}
print(f"https://nestservices.google.com/partnerconnections/{os.environ['PROJECT']}/auth?" + urllib.parse.urlencode(q))
PY
)
echo
echo "1. Open this link on your phone or computer, sign in with the Google account your Nest is on,"
echo "   allow access, and pick your thermostat:"
echo
echo "   $URL"
echo
echo "2. Google then opens google.com with ?code=... in the address. Copy that whole address (or just the code)."
read -rsp "3. Paste it here (hidden), then Enter: " PASTED; echo
export PASTED

RESULT=$(python3 - <<'PY'
import json, os, sys, urllib.parse, urllib.request
pasted = os.environ["PASTED"].strip()
code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [pasted])[0]
body = urllib.parse.urlencode({"client_id": os.environ["CLIENT_ID"], "client_secret": os.environ["CLIENT_SECRET"],
                               "code": code, "grant_type": "authorization_code",
                               "redirect_uri": "https://www.google.com"}).encode()
try:
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body), timeout=30) as r:
        got = json.load(r)
except urllib.error.HTTPError as e:
    print("ERR " + e.read(300).decode("utf-8", "replace").replace("\n", " ")); sys.exit(0)
token = got.get("refresh_token", "")
print("OK " + token if token else "ERR Google gave no refresh token (use the link again: it asks for consent)")
PY
)
if [[ $RESULT != OK* ]]; then
  echo "Google didn't accept it: ${RESULT#ERR }"
  echo "Check the redirect URI (https://www.google.com) on the OAuth client, then run this again."
  exit 1
fi
export REFRESH="${RESULT#OK }"

python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path = sys.argv[1]
values = {"BUDDY_NEST_PROJECT_ID": os.environ["PROJECT"], "BUDDY_NEST_CLIENT_ID": os.environ["CLIENT_ID"],
          "BUDDY_NEST_CLIENT_SECRET": os.environ["CLIENT_SECRET"], "BUDDY_NEST_REFRESH_TOKEN": os.environ["REFRESH"]}
text = open(path).read()
for name, value in values.items():
    line = f"{name}={value.strip()}"
    if re.search(rf"(?m)^{name}=", text):
        text = re.sub(rf"(?m)^{name}=.*$", lambda m: line, text)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
print("Saved the Nest link (4 settings).")
PY
unset CLIENT_SECRET REFRESH PASTED
secrets_seal
systemctl restart buddy
echo "Buddy restarted. In the app: Menu > Connections > Home > Google Nest > Test. Or ask Buddy: \"what's the temperature at home?\""
