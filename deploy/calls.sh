#!/usr/bin/env bash
# Set up Buddy's phone calls (Buddy Connect, Calls group), once:
#   sudo bash /opt/buddy/deploy/calls.sh
# Before: a Plivo account with a US phone number (plivo.com: Auth ID and Auth Token on the console's home page),
# a Deepgram API key (console.deepgram.com) and an ElevenLabs API key (elevenlabs.io, Developers > API keys).
# Your Tailscale policy must allow Funnel for the server (deploy/tailscale-policy.hujson, "nodeAttrs").
#
# It installs the call engine (Pipecat), asks for the keys and the facts a call may share (all hidden while you
# type; press Enter to keep one that's already saved), and publishes ONLY /calls on port 8443 to the internet
# through Tailscale Funnel, so Plivo can reach it. Buddy's app stays tailnet-only on 443. Nothing is printed back.
set -euo pipefail
APP=/opt/buddy
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"

echo "Installing the call engine (Pipecat with Deepgram, ElevenLabs and Silero; a few minutes the first time)..."
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q --upgrade -r "$APP/backend/requirements-calls.txt"

secrets_open
have() { grep -qE "^$1=.+" "$ENV_FILE"; }
ask() {  # ask NAME "question" [secret]: hidden input; Enter keeps a saved value
  local name=$1 question=$2 value keep=""
  have "$name" && keep=" (Enter keeps the saved one)"
  read -rsp "$question$keep: " value; echo
  if [[ -z $value ]]; then
    have "$name" || { echo "  $name is needed."; exit 1; }
    return
  fi
  NAME="$name" VALUE="$value" python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path, name = sys.argv[1], os.environ["NAME"]
value = os.environ["VALUE"].strip().replace("\n", "")
text = open(path).read()
line = f"{name}={value}"
if re.search(rf"(?m)^{name}=", text):
    text = re.sub(rf"(?m)^{name}=.*$", lambda m: line, text)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY
}

echo
echo "Plivo (places the calls):"
ask BUDDY_PLIVO_AUTH_ID "  Auth ID (starts MA or SA)"
ask BUDDY_PLIVO_AUTH_TOKEN "  Auth Token"
ask BUDDY_PLIVO_NUMBER "  Your Plivo phone number, e.g. +12015550100"
echo "Deepgram (hears the other side):"
ask BUDDY_DEEPGRAM_KEY "  API key"
echo "ElevenLabs (Buddy's voice):"
ask BUDDY_ELEVENLABS_KEY "  API key"
echo
echo "What a call may say about you (DOB and insurance only on appointment calls; never card numbers or SSN):"
ask BUDDY_OWNER_FULL_NAME "  Your full name"
ask BUDDY_OWNER_PHONE "  Your mobile number (the callback number, and where hold calls ring you)"
ask BUDDY_OWNER_DOB "  Date of birth, e.g. January 2, 1990"
ask BUDDY_OWNER_INSURANCE "  Health insurance, e.g. Aetna PPO, member ID W123456789"

echo
echo "Caller ID: the businesses can see your own mobile number instead of Buddy's Plivo number, once you've"
echo "verified it in Plivo's console (Verified Caller IDs: Plivo calls or texts you a code)."
read -rp "  Show your own number? Only say y after verifying it [y/N]: " MINE
MINE_VALUE=0; [[ ${MINE,,} == y* ]] && MINE_VALUE=1
python3 - "$ENV_FILE" "$MINE_VALUE" <<'PY'
import re, sys
path, value = sys.argv[1], sys.argv[2]
text = open(path).read()
line = f"BUDDY_CALL_SHOW_MY_NUMBER={value}"
text = re.sub(r"(?m)^BUDDY_CALL_SHOW_MY_NUMBER=.*$", lambda m: line, text) if "BUDDY_CALL_SHOW_MY_NUMBER=" in text \
    else text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY

echo
echo "Publishing /calls (only) to the internet with Tailscale Funnel on port 8443..."
HOST=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
if ! tailscale funnel --bg --https=8443 --set-path=/calls http://127.0.0.1:8000/calls; then
  echo "Tailscale refused Funnel. Add this to your Tailscale policy (admin console > Access controls), then run"
  echo "this script again:"
  echo '  "nodeAttrs": [{"target": ["tag:buddy-server"], "attr": ["funnel"]}],'
  exit 1
fi
PUBLIC="https://$HOST:8443"
PLIVO_DIGITS=$(grep -E '^BUDDY_PLIVO_NUMBER=' "$ENV_FILE" | cut -d= -f2- | tr -cd '0-9')
python3 - "$ENV_FILE" "$PUBLIC" <<'PY'
import re, sys
path, value = sys.argv[1], sys.argv[2]
text = open(path).read()
line = f"BUDDY_CALLS_PUBLIC_URL={value}"
text = re.sub(r"(?m)^BUDDY_CALLS_PUBLIC_URL=.*$", lambda m: line, text) if "BUDDY_CALLS_PUBLIC_URL=" in text \
    else text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
PY
secrets_seal
systemctl restart buddy
echo
echo "Done. Public address for Plivo: $PUBLIC/calls (only that path; every request needs the call's token and"
echo "Plivo's signature)."
echo
echo "Buddy answers the calls you miss: on your phone, dial each of these and press Call (T-Mobile and most US"
echo "carriers; each replaces your voicemail for that case):"
echo "  **61*${PLIVO_DIGITS}**20#   you don't answer within 20 seconds"
echo "  **67*${PLIVO_DIGITS}#       you're on another call"
echo "  **62*${PLIVO_DIGITS}#       your phone is off or has no signal"
echo "  To stop and go back to voicemail: ##004#"
echo "Check: in the app, Menu > Connections > Calls > Test on each. Then ask Buddy, e.g. \"call Dr. Mehta's office"
echo "to book a checkup next week\". It always asks you first."
