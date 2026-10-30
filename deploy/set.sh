#!/usr/bin/env bash
# Add or change one of Buddy's settings without editing files by hand:
#   sudo bash /opt/buddy/deploy/set.sh BUDDY_EMAIL_ADDRESS
# It asks for the value (hidden while you type or paste), saves it in
# /etc/buddy/buddy.env and restarts Buddy. Run with no name to list what's set.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }
source "$(dirname "$(readlink -f "$0")")/secrets-lib.sh"
secrets_open
if [[ $# -eq 0 ]]; then
  echo "Settings in $ENV_FILE (values hidden):"
  grep -E '^[A-Z_]+=' "$ENV_FILE" | cut -d= -f1 | sed 's/^/  /'
  exit 0
fi
NAME="$1"
[[ "$NAME" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "Setting names look like BUDDY_EMAIL_ADDRESS."; exit 1; }
read -rsp "Value for $NAME (hidden; paste, then Enter): " VALUE; echo
NAME="$NAME" VALUE="$VALUE" python3 - "$ENV_FILE" <<'PY'
import os, re, sys
path, name = sys.argv[1], os.environ["NAME"]
value = os.environ["VALUE"].strip().replace("\n", "")
text = open(path).read()
line = f"{name}={value}"
if re.search(rf"(?m)^{name}=", text):
    text = re.sub(rf"(?m)^{name}=.*$", lambda m: line, text)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
open(path, "w").write(text)
shown = value if len(value) <= 12 else value[:6] + "..." + f" ({len(value)} characters)"
print(f"Saved {name}: {shown}" if value else f"Cleared {name}")
PY
secrets_seal
systemctl restart buddy
echo "Buddy restarted."
