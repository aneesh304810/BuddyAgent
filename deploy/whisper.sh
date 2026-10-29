#!/usr/bin/env bash
# Install Whisper speech recognition on Buddy's server (optional, about 500 MB):
#   sudo bash /opt/buddy/deploy/whisper.sh          # the "base" model (2 GB server)
#   sudo bash /opt/buddy/deploy/whisper.sh small    # more accurate; needs about 4 GB of memory
# Then in the Buddy app: Settings -> Speech recognition -> Your server.
set -euo pipefail
APP=/opt/buddy
MODEL="${1:-base}"
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
echo "==> Installing faster-whisper"
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q "faster-whisper>=1.1"
echo "==> Downloading the $MODEL model"
sudo -u buddy HOME=/var/lib/buddy "$APP/backend/.venv/bin/python" -c \
  "from faster_whisper import WhisperModel; WhisperModel('$MODEL', device='cpu', compute_type='int8')"
bash "$APP/deploy/set.sh" BUDDY_WHISPER_MODEL <<< "$MODEL" >/dev/null
echo "    Whisper ($MODEL) is ready. Buddy restarted."
