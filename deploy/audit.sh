#!/usr/bin/env bash
# Check Buddy's server for known vulnerabilities in its Python packages:
#   sudo bash /opt/buddy/deploy/audit.sh
# Fix what it finds with: sudo bash /opt/buddy/deploy/update.sh
set -euo pipefail
APP=/opt/buddy
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q --upgrade pip-audit
echo "==> Python packages"
sudo -u buddy "$APP/backend/.venv/bin/pip-audit" || true
echo "==> Server checks"
stat -c '%a %U:%G %n' /etc/buddy/buddy.env
ufw status | head -5
echo "Public SSH should be closed; settings file should be 600 root:root."
