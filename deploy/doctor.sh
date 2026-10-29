#!/usr/bin/env bash
# Why is Buddy slow or not answering? Run on the server:
#   sudo bash /opt/buddy/deploy/doctor.sh
set -uo pipefail
echo "==> Is Buddy running?"
systemctl is-active buddy && curl -s --max-time 5 http://127.0.0.1:8000/health; echo
echo "==> Memory (Buddy needs a few hundred MB free; Whisper adds ~300 MB)"
free -m | head -2
echo "==> Busiest programs"
ps -eo pid,%cpu,%mem,rss,comm --sort=-%mem | head -6
echo "==> How long replies took (last 15)"
journalctl -u buddy --since "-24h" --no-pager | grep -o "reply took [0-9.]*s.*" | tail -15
echo "==> Whisper times (last 10)"
journalctl -u buddy --since "-24h" --no-pager | grep -o "whisper took [0-9.]*s" | tail -10
echo "==> Problems in the last 24 hours"
journalctl -u buddy --since "-24h" --no-pager | grep -iE "error|warning|exception|traceback|killed|timeout|overloaded|529|rate" | tail -15
echo "==> Restarts or out-of-memory kills"
journalctl -u buddy --since "-24h" --no-pager | grep -iE "Started|Stopped|oom|Main process exited" | tail -6
journalctl -k --since "-24h" --no-pager | grep -i "out of memory" | tail -3
echo "==> Tailscale"
tailscale status | head -5
