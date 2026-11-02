#!/usr/bin/env bash
# Access control for Buddy's server (policy: docs/ACCESS_CONTROL.md):
#   sudo bash /opt/buddy/deploy/access.sh review              quarterly access review (saves evidence)
#   sudo bash /opt/buddy/deploy/access.sh list                who and what has access now
#   sudo bash /opt/buddy/deploy/access.sh revoke user|key|device NAME
#   sudo bash /opt/buddy/deploy/access.sh offboard NAME       everything for someone leaving, plus a new app token
#   sudo bash /opt/buddy/deploy/access.sh install             daily automatic revoke of unapproved accounts/keys
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
HERE="$(dirname "$(readlink -f "$0")")"
case "${1:-list}" in
  install)
    cat > /etc/systemd/system/buddy-access.service <<UNIT
[Unit]
Description=Buddy: lock server accounts and SSH keys that aren't on the approved access list

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HERE/access.py enforce
UNIT
    cat > /etc/systemd/system/buddy-access.timer <<'UNIT'
[Unit]
Description=Buddy: daily access enforcement

[Timer]
OnCalendar=*-*-* 02:17
RandomizedDelaySec=20min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload
    systemctl enable --now buddy-access.timer >/dev/null
    echo "Daily access enforcement: on. Run your first review now if you haven't: sudo bash $HERE/access.sh review" ;;
  offboard)
    python3 "$HERE/access.py" "$@"
    echo; echo "New app token (old links stop working):"
    bash "$HERE/security.sh" rotate-token ;;
  *) exec python3 "$HERE/access.py" "$@" ;;
esac
