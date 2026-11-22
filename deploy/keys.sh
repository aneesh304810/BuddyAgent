#!/usr/bin/env bash
# Test every key Buddy has: one tiny real request to each service, and the provider's own answer when one fails.
# Ends with the count: working, not working, no key, set but not tested.
#   sudo bash /opt/buddy/deploy/keys.sh            all of them (takes a minute or two)
#   sudo bash /opt/buddy/deploy/keys.sh list       every key the code reads: set, missing, or not needed (no calls)
#   sudo bash /opt/buddy/deploy/keys.sh github     every hop of one request to one provider (to see why it fails)
# Keys are read from Buddy's settings (decrypted in memory if sealed) and never printed.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
APP=$(dirname "$HERE")
# shellcheck source=secrets-lib.sh
. "$HERE/secrets-lib.sh"
secrets_open
cd "$APP/backend"
if [[ $# -eq 0 ]]; then
    # Buddy itself runs the tests when it is up: it holds the sign-ins some services need (Oura, Nest).
    if OUT=$(curl -fsS --max-time 600 -H "Authorization: Bearer $(secrets_get BUDDY_TOKEN)" \
                  http://127.0.0.1:8000/admin/keys 2>/dev/null) && [[ -n $OUT ]]; then
        echo "Testing Buddy's keys (one tiny request each; keys are never shown):"
        echo "$OUT"
        exit 0
    fi
    echo "Buddy is not running (or is an older version): testing from the settings alone."
fi
"$APP/backend/.venv/bin/python" -m buddy.keytest "$ENV_FILE" ${1:+"$1"}
