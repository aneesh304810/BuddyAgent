#!/usr/bin/env bash
# Noise removal for Talk mode's live hearing: a small model (GTCRN, half a megabyte) that runs on this server.
# It is optional and off by default: Deepgram, which does the hearing, advises sending speech unaltered, because
# cleaning usually makes recognition worse. To try it:
#   sudo bash /opt/buddy/deploy/noise.sh                     # install the model (once)
#   sudo bash /opt/buddy/deploy/set.sh BUDDY_NOISE_CANCEL    # type: talk   (to stop: run it again and type: off)
set -euo pipefail
APP=/opt/buddy
DATA=/var/lib/buddy
MODEL=gtcrn_simple.onnx
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/$MODEL"
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
sudo -u buddy "$APP/backend/.venv/bin/pip" install -q "sherpa-onnx>=1.12"
install -d -o buddy -g buddy -m 700 "$DATA/models"
if [[ ! -s "$DATA/models/$MODEL" ]]; then
  curl -fsSL --retry 3 -o "$DATA/models/$MODEL.part" "$URL"
  mv "$DATA/models/$MODEL.part" "$DATA/models/$MODEL"
  chown buddy:buddy "$DATA/models/$MODEL"; chmod 600 "$DATA/models/$MODEL"
fi
echo "  The noise removal model is installed. It is off until you switch it on:"
echo "    sudo bash $APP/deploy/set.sh BUDDY_NOISE_CANCEL     (type: talk)"
