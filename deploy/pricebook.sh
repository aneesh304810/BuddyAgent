#!/usr/bin/env bash
# The price book (prices and deals, for history and predictions): is it readable, and rebuild it if not.
#   sudo bash /opt/buddy/deploy/pricebook.sh            # check: the database engine's version, each table, the
#                                                        # statements the Deals page depends on. Changes nothing.
#   sudo bash /opt/buddy/deploy/pricebook.sh rebuild    # write everything into a new file; the old file is kept
#                                                        # beside it as pricebook.duckdb.before-rebuild
# Buddy rebuilds the file by itself, once, when the database engine fails twice on the same statement. This script
# is for looking, and for doing it by hand. Buddy must be running: only one program may have the price book open.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open
AUTH="Authorization: Bearer $(secrets_get BUDDY_TOKEN)"
case "${1:-check}" in
  check)
    curl -fsS -m 120 -H "$AUTH" "http://127.0.0.1:8000/admin/pricebook?text=1" || echo "Couldn't reach Buddy. Is it running?"
    echo
    ;;
  rebuild)
    echo "Rebuilding the price book (this can take a minute)..."
    curl -fsS -m 900 -X POST -H "$AUTH" "http://127.0.0.1:8000/admin/pricebook/rebuild" || echo "Couldn't reach Buddy. Is it running?"
    echo
    ;;
  *)
    echo "Use: pricebook.sh [check | rebuild]"; exit 1
    ;;
esac
