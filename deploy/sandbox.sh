#!/usr/bin/env bash
# The code sandbox: a workspace on this server where Buddy can clone repos and run code
# for you, in throwaway containers that can't touch Buddy, your keys or your data.
#
#   sudo bash /opt/buddy/deploy/sandbox.sh          install or update (about 5 minutes the first time)
#   sudo bash /opt/buddy/deploy/sandbox.sh token    set the GitHub token for private repos and pushing
#   sudo bash /opt/buddy/deploy/sandbox.sh status   is it running, workspaces and disk use
#   sudo bash /opt/buddy/deploy/sandbox.sh off      stop it (workspaces are kept)
#
# What it sets up: Docker; a separate user (buddy-sandbox) that alone can use it; the
# sandbox image (Python, Node, git); a network for the containers that reaches the internet
# but not this server, your tailnet, private networks or the cloud metadata service; and the
# sandbox service Buddy talks to. The token lives in /etc/buddy-sandbox/sandbox.env (root
# only), not in Buddy's settings, and is used only to clone and push.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE=/etc/buddy-sandbox/sandbox.env

case "${1:-install}" in
  token)
    echo "Make a fine-grained GitHub token for ONLY the repos Buddy may work on:"
    echo "  github.com -> Settings -> Developer settings -> Fine-grained tokens -> Generate new token"
    echo "  Repository access: Only select repositories.  Permissions: Contents: Read and write."
    read -rsp "Paste the token (hidden), then Enter: " TOKEN; echo
    [[ "$TOKEN" =~ ^(github_pat_|ghp_)[A-Za-z0-9_]+$ ]] || { echo "That doesn't look like a GitHub token."; exit 1; }
    mkdir -p /etc/buddy-sandbox && touch "$ENV_FILE" && chmod 600 "$ENV_FILE"
    grep -v '^SANDBOX_GITHUB_TOKEN=' "$ENV_FILE" > "$ENV_FILE.new" || true
    echo "SANDBOX_GITHUB_TOKEN=$TOKEN" >> "$ENV_FILE.new" && mv "$ENV_FILE.new" "$ENV_FILE" && chmod 600 "$ENV_FILE"
    systemctl restart buddy-sandbox && echo "Saved. The sandbox can now clone your private repos and push buddy/... branches."
    exit 0 ;;
  status)
    systemctl is-active buddy-sandbox || true
    python3 - <<'PY' || true
import json, socket
s = socket.socket(socket.AF_UNIX)
s.connect("/run/buddy-sandbox/sandbox.sock")
s.sendall(b'{"op": "status"}\n')
print(json.dumps(json.loads(s.makefile().readline()), indent=2))
PY
    df -h /var/lib/docker /var/lib/buddy-sandbox 2>/dev/null | tail -n +1
    exit 0 ;;
  off)
    systemctl disable --now buddy-sandbox && echo "Sandbox stopped. Workspaces are kept in /var/lib/buddy-sandbox."
    exit 0 ;;
  install) ;;
  *) echo "Use: sandbox.sh [install|token|status|off]"; exit 1 ;;
esac

echo "== Docker"
command -v docker >/dev/null || { apt-get update -q && apt-get install -y -q docker.io; }
systemctl enable --now docker

echo "== The sandbox's own user"
id buddy-sandbox >/dev/null 2>&1 || useradd --system --home-dir /var/lib/buddy-sandbox --shell /usr/sbin/nologin buddy-sandbox
usermod -aG docker,buddy buddy-sandbox
mkdir -p /var/lib/buddy-sandbox/ws /etc/buddy-sandbox
chown -R buddy-sandbox:buddy-sandbox /var/lib/buddy-sandbox && chmod 700 /var/lib/buddy-sandbox
touch "$ENV_FILE" && chmod 600 "$ENV_FILE"

echo "== The sandbox image (Python, Node, git)"
docker build -q -t buddy-sandbox:latest "$HERE/sandbox"

echo "== A network that can't reach this server, the tailnet or the metadata service"
docker network inspect buddy-sandbox >/dev/null 2>&1 || \
  docker network create --opt com.docker.network.bridge.name=buddysbx buddy-sandbox >/dev/null
cat > /usr/local/sbin/buddy-sandbox-firewall <<'FW'
#!/bin/sh
# Containers on the sandbox network: internet yes; this host, private ranges, tailnet, metadata no.
iptables -C INPUT -i buddysbx -j DROP 2>/dev/null || iptables -I INPUT -i buddysbx -j DROP
for net in 169.254.0.0/16 100.64.0.0/10 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
  iptables -C DOCKER-USER -i buddysbx -d "$net" -j DROP 2>/dev/null || iptables -I DOCKER-USER -i buddysbx -d "$net" -j DROP
done
FW
chmod 755 /usr/local/sbin/buddy-sandbox-firewall
cat > /etc/systemd/system/buddy-sandbox-firewall.service <<'UNIT'
[Unit]
Description=Firewall rules for Buddy's code sandbox
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/buddy-sandbox-firewall
RemainAfterExit=true

[Install]
WantedBy=multi-user.target
UNIT

echo "== The sandbox service"
# A root-owned copy: /opt/buddy belongs to the buddy user, and the sandbox service must not run
# code Buddy itself could change (it's the one allowed to use Docker).
install -d -o root -g root -m 755 /usr/local/lib/buddy-sandbox
install -o root -g root -m 755 "$HERE/sandbox/sandboxd.py" /usr/local/lib/buddy-sandbox/sandboxd.py
cat > /etc/systemd/system/buddy-sandbox.service <<UNIT
[Unit]
Description=Buddy's code sandbox
After=docker.service buddy-sandbox-firewall.service
Requires=docker.service buddy-sandbox-firewall.service

[Service]
User=buddy-sandbox
Group=buddy-sandbox
SupplementaryGroups=docker buddy
EnvironmentFile=-$ENV_FILE
RuntimeDirectory=buddy-sandbox
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 /usr/local/lib/buddy-sandbox/sandboxd.py
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/buddy-sandbox

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now buddy-sandbox-firewall buddy-sandbox
systemctl restart buddy-sandbox
sleep 2
systemctl restart buddy
echo
echo "Done. The sandbox is running. Ask Buddy: \"clone github.com/you/project and run its tests\"."
echo "For private repos and pushing branches: sudo bash $HERE/sandbox.sh token"
