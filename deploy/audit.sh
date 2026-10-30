#!/usr/bin/env bash
# Check Buddy's server without changing anything (known vulnerabilities, keys, network, updates...):
#   sudo bash /opt/buddy/deploy/audit.sh
# To patch as well: sudo bash /opt/buddy/deploy/security.sh check
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
exec python3 "$(dirname "$(readlink -f "$0")")/maintain.py" --check-only --pretty
