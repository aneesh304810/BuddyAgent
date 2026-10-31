#!/usr/bin/env bash
# Install voice match (Talk mode answers only your voice): a small speaker-recognition model
# (sherpa-onnx, about 25 MB). update.sh runs this by itself; run it again to repair:
#   sudo bash /opt/buddy/deploy/voice.sh
# Then in the Buddy app: Settings -> Teach Buddy my voice.
set -euo pipefail
APP=/opt/buddy
DATA=/var/lib/buddy
MODEL=wespeaker_en_voxceleb_resnet34.onnx
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/$MODEL"
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q "sherpa-onnx>=1.10"
install -d -o buddy -g buddy -m 700 "$DATA/models"
if [[ ! -s "$DATA/models/$MODEL" ]]; then
  curl -fsSL --retry 3 -o "$DATA/models/$MODEL.part" "$URL"
  mv "$DATA/models/$MODEL.part" "$DATA/models/$MODEL"
  chown buddy:buddy "$DATA/models/$MODEL"; chmod 600 "$DATA/models/$MODEL"
fi
echo "  Voice match is installed. In the app: Settings -> Teach Buddy my voice."
