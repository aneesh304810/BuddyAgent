#!/usr/bin/env bash
# Buddy's security and maintenance, in one place:
#
#   sudo bash /opt/buddy/deploy/security.sh check          patch now and check everything (also runs weekly)
#   sudo bash /opt/buddy/deploy/security.sh report         the last check's findings, without patching
#   sudo bash /opt/buddy/deploy/security.sh seal           encrypt your keys at rest (no plain copy on disk)
#   sudo bash /opt/buddy/deploy/security.sh unseal         back to the plain root-only file
#   sudo bash /opt/buddy/deploy/security.sh rotate-token   a new access token for the app (prints the new link)
#   sudo bash /opt/buddy/deploy/security.sh harden         extra service lockdown (update.sh applies it)
#   sudo bash /opt/buddy/deploy/security.sh soften         remove that lockdown, if something needs it
#   sudo bash /opt/buddy/deploy/security.sh install        weekly timer and auto-reboot for updates (update.sh runs it)
#   sudo bash /opt/buddy/deploy/security.sh link           the app link (server address + access token)
#   sudo bash /opt/buddy/deploy/security.sh edit           edit all settings in nano (sealed or not), then restart
#   sudo bash /opt/buddy/deploy/security.sh get NAME       print one setting's value
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
HERE="$(dirname "$(readlink -f "$0")")"
source "$HERE/secrets-lib.sh"
DROPINS=(/etc/systemd/system/buddy.service.d /etc/systemd/system/buddy-backup.service.d)

wait_healthy() {
  for _ in $(seq 40); do curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0; sleep 1; done
  return 1
}

install_hardening() {
  mkdir -p /etc/systemd/system/buddy.service.d
  cat > /etc/systemd/system/buddy.service.d/hardening.conf <<'UNIT'
# Extra lockdown for Buddy's service (remove with: security.sh soften).
[Service]
CapabilityBoundingSet=
AmbientCapabilities=
PrivateDevices=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
UMask=0077
KeyringMode=private
RemoveIPC=true
UNIT
}

case "${1:-check}" in
  check)
    python3 "$HERE/maintain.py" --pretty ;;
  report)
    python3 "$HERE/maintain.py" --check-only >/dev/null && cat /var/lib/buddy/security.json ;;
  seal)
    command -v systemd-creds >/dev/null || { echo "This server's systemd is too old for encrypted settings."; exit 1; }
    secrets_sealed && { echo "Already sealed."; exit 0; }
    [[ -f $PLAIN_ENV ]] || { echo "No settings file found."; exit 1; }
    systemd-creds setup >/dev/null 2>&1 || true   # this machine's own key (root only)
    systemd-creds encrypt --name=buddy-env "$PLAIN_ENV" "$SEALED_ENV.new"
    chmod 600 "$SEALED_ENV.new" && mv "$SEALED_ENV.new" "$SEALED_ENV"
    for d in "${DROPINS[@]}"; do
      mkdir -p "$d"
      printf '[Service]\nEnvironmentFile=\nLoadCredentialEncrypted=buddy-env:%s\n' "$SEALED_ENV" > "$d/secrets.conf"
    done
    systemctl daemon-reload && systemctl restart buddy
    if wait_healthy; then
      shred -u "$PLAIN_ENV"
      echo "Sealed. Your keys are encrypted on disk; only Buddy's services can decrypt them, into memory."
      echo "set.sh, email.sh and bank.sh keep working as before."
    else
      rm -f "${DROPINS[@]/%//secrets.conf}" "$SEALED_ENV"; systemctl daemon-reload; systemctl restart buddy
      echo "Buddy didn't start with sealed settings, so nothing changed (the plain file is still there)."
      journalctl -u buddy -n 15 --no-pager; exit 1
    fi ;;
  unseal)
    secrets_sealed || { echo "Not sealed."; exit 0; }
    umask 077
    systemd-creds decrypt --name=buddy-env "$SEALED_ENV" "$PLAIN_ENV.new" && mv "$PLAIN_ENV.new" "$PLAIN_ENV"
    chmod 600 "$PLAIN_ENV"
    rm -f "${DROPINS[@]/%//secrets.conf}" "$SEALED_ENV"
    systemctl daemon-reload && systemctl restart buddy && echo "Back to the plain root-only settings file." ;;
  rotate-token)
    secrets_open
    NEW=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
    NEW="$NEW" python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path = sys.argv[1]
text = open(path).read()
line = "BUDDY_TOKEN=" + os.environ["NEW"]
text = re.sub(r"(?m)^BUDDY_TOKEN=.*$", line, text) if re.search(r"(?m)^BUDDY_TOKEN=", text) else text + line + "\n"
open(path, "w").write(text)
PY
    secrets_seal
    systemctl restart buddy
    HOST=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' || echo "your-server")
    echo "New token set; the old one stopped working. Open this once on each device (then it's remembered):"
    echo "  https://$HOST/?token=$NEW"
    echo "In the Android app: Settings -> server link, paste the same link." ;;
  harden)
    install_hardening && rm -f /etc/buddy/no-hardening
    systemctl daemon-reload && systemctl restart buddy
    if wait_healthy; then echo "Hardened."; systemd-analyze security buddy.service --no-pager | tail -1
    else rm -f /etc/systemd/system/buddy.service.d/hardening.conf; systemctl daemon-reload; systemctl restart buddy
      echo "Buddy didn't start with the extra lockdown, so it was removed again."; exit 1; fi ;;
  link)
    secrets_open
    HOST=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' || echo "your-server")
    echo "https://$HOST/?token=$(secrets_get BUDDY_TOKEN)" ;;
  get)
    [[ -n ${2:-} ]] || { echo "Use: security.sh get NAME"; exit 1; }
    secrets_open
    secrets_get "$2" ;;
  edit)
    secrets_open
    "${EDITOR:-nano}" "$ENV_FILE"
    secrets_seal
    systemctl restart buddy && echo "Saved$(secrets_sealed && echo ' (still encrypted)') and Buddy restarted." ;;
  harden-file)  # only write the lockdown file (update.sh restarts and checks)
    install_hardening ;;
  soften)
    rm -f /etc/systemd/system/buddy.service.d/hardening.conf && touch /etc/buddy/no-hardening
    systemctl daemon-reload && systemctl restart buddy && echo "Extra lockdown removed (harden puts it back)." ;;
  install)
    # Security updates reboot by themselves at 4:30am when needed (Buddy starts again on boot).
    cat > /etc/apt/apt.conf.d/52buddy-unattended <<'APT'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
APT
    cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
    cat > /etc/systemd/system/buddy-maintain.service <<UNIT
[Unit]
Description=Weekly patching and security check for Buddy
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HERE/maintain.py
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=2h
UNIT
    cat > /etc/systemd/system/buddy-maintain.timer <<'UNIT'
[Unit]
Description=Weekly patching and security check for Buddy

[Timer]
OnCalendar=Sun *-*-* 03:47
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    # Logs: kept a month, at most 500 MB.
    mkdir -p /etc/systemd/journald.conf.d
    printf '[Journal]\nMaxRetentionSec=1month\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/buddy.conf
    systemctl restart systemd-journald || true
    systemctl daemon-reload
    systemctl enable --now buddy-maintain.timer >/dev/null
    echo "Weekly patching and security check: on (Sundays around 4am)." ;;
  *) echo "Unknown command: ${1:-}"; sed -n '2,14p' "$0"; exit 1 ;;
esac
