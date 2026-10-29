#!/usr/bin/env bash
# Watch every message between your phone and Buddy, live:
#   sudo bash /opt/buddy/deploy/trace.sh        (Ctrl+C to stop)
#
#   ←  phone to Buddy:  user (what you said), ambient (room talk in Listen-in),
#                       audio (speech for Whisper), location, notification...
#   →  Buddy to phone:  thinking (got it), say (each sentence), done, whisper,
#                       nudge, approval, heard (Whisper's text), error
#   ·  connected / disconnected
# Tracing stops when you press Ctrl+C. While it runs, what you say is written to
# the system log, so don't leave it on.
set -uo pipefail
ENV_FILE=/etc/buddy/buddy.env
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
TOKEN=$(grep '^BUDDY_TOKEN=' "$ENV_FILE" | cut -d= -f2)
toggle() { curl -s -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/admin/trace?on=$1" >/dev/null; }
toggle true || { echo "Buddy isn't answering on this server."; exit 1; }
trap 'toggle false; echo; echo "Tracing off."' EXIT INT TERM
echo "Tracing on. Talk to Buddy now; Ctrl+C to stop."
echo "Reading it: no '← user' or '← ambient' line = your words never reached the server"
echo "(phone, earbud mic or internet). '← ambient' = it arrived as room talk, not a question."
echo
journalctl -fu buddy -o cat --since now | grep --line-buffered -E "buddy\.trace|reply took|whisper took|Claude API error|Traceback|ERROR" \
  | sed -u -E 's/^[0-9-]+ ([0-9:]+),[0-9]+ buddy\.trace /\1 /'
