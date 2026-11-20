#!/usr/bin/env bash
# Buddy's modules: which parts are switched on for this server (backend/buddy/modules.py, docs/PLATFORM.md).
#   sudo bash /opt/buddy/deploy/modules.sh                 # list the modules and which are on
#   sudo bash /opt/buddy/deploy/modules.sh off investing   # switch one off (Buddy restarts)
#   sudo bash /opt/buddy/deploy/modules.sh on investing    # switch it back on
#   sudo bash /opt/buddy/deploy/modules.sh statement       # this month: what each module was used for and cost
#   sudo bash /opt/buddy/deploy/modules.sh statement 2026-11
# The switch is the setting BUDDY_MODULES. Unset means everything Buddy has is on. Core is always on.
# A module that is off has no tools in the conversation and its background loop does not start.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
APP=/opt/buddy
PY="$APP/backend/.venv/bin/python"
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open
CURRENT="$(secrets_get BUDDY_MODULES)"
case "${1:-list}" in
  list)
    (cd "$APP/backend" && sudo -u buddy "$PY" -m buddy.modules list "$CURRENT")
    ;;
  on|off)
    [[ $# -ge 2 ]] || { echo "Which module? Run with no arguments to see them."; exit 1; }
    NEW="$(cd "$APP/backend" && sudo -u buddy "$PY" -m buddy.modules "$1" "$2" "$CURRENT")"
    secrets_set BUDDY_MODULES "$NEW"
    secrets_seal
    systemctl restart buddy
    echo "BUDDY_MODULES=$NEW"
    echo "Buddy restarted."
    ;;
  statement)
    curl -fsS -H "Authorization: Bearer $(secrets_get BUDDY_TOKEN)" \
      "http://127.0.0.1:8000/admin/modules?month=${2:-}&text=1" || echo "Couldn't reach Buddy. Is it running?"
    echo
    ;;
  *)
    echo "Use: modules.sh [list | on <module> | off <module> | statement [YYYY-MM]]"; exit 1
    ;;
esac
