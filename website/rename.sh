#!/usr/bin/env bash
# Change the company name and contact email on every page:
#   bash rename.sh "New Company Name" [new-email@yourdomain.com]
set -euo pipefail
cd "$(dirname "$0")"
OLD_NAME="Buddy Labs"; OLD_EMAIL="aneeshnair304810@gmail.com"
NEW_NAME="${1:-}"; NEW_EMAIL="${2:-$OLD_EMAIL}"
INITIAL="${NEW_NAME:0:1}"
[[ -n $NEW_NAME ]] || { echo "Usage: bash rename.sh \"New Name\" [email]"; exit 1; }
for f in *.html; do
  python3 - "$f" "$OLD_NAME" "$NEW_NAME" "$OLD_EMAIL" "$NEW_EMAIL" "$INITIAL" <<'PY'
import sys
path, old, new, old_mail, new_mail, initial = sys.argv[1:]
s = open(path).read().replace(old, new).replace(old_mail, new_mail)
import re
s = re.sub(r'(<span class="mark" aria-hidden="true">)[^<]*(</span>)', lambda m: m.group(1) + initial + m.group(2), s)
open(path, "w").write(s)
PY
done
sed -i -E "s/>[^<]*<\/text>/>$INITIAL<\/text>/" favicon.svg
sed -i "s/^OLD_NAME=\"[^\"]*\"; OLD_EMAIL=\"[^\"]*\"/OLD_NAME=\"$NEW_NAME\"; OLD_EMAIL=\"$NEW_EMAIL\"/" rename.sh
echo "Renamed to $NEW_NAME ($NEW_EMAIL)."
