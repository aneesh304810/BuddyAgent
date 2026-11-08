#!/usr/bin/env bash
# Test every key Buddy has: one tiny real request to each service, and the provider's own answer when one fails.
#   sudo bash /opt/buddy/deploy/keys.sh            all of them
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
"$APP/backend/.venv/bin/python" -m buddy.keytest "$ENV_FILE" ${1:+"$1"}
