#!/usr/bin/env bash
# Self-deploy: after the owner's yes, Buddy's own fix (a buddy/... branch) is checked, tested, merged and deployed,
# with a rollback if Buddy doesn't come back healthy. Off until the owner turns it on:
#   sudo bash /opt/buddy/deploy/selfdeploy.sh on       turn on (installs the root-owned watcher)
#   sudo bash /opt/buddy/deploy/selfdeploy.sh off      turn off
#   sudo bash /opt/buddy/deploy/selfdeploy.sh status
#
# How it stays safe:
#   - Buddy only drops a request (the branch name) in /var/lib/buddy/deploy/, and only after the owner's yes, read
#     in code. Everything else runs here, as root, from a root-owned copy of this script Buddy can't change.
#   - Changes to Buddy's guard rails are never self-deployed: deploy scripts, CI, dependencies, budgets, approvals,
#     privacy, the sandbox, self-code, config; and no test may be deleted. Those need the owner to merge on GitHub.
#   - The merged code's tests run first (as the buddy user, no secrets). Any failure: nothing is merged.
#   - Merged on GitHub (so the server and GitHub agree), deployed with update.sh, then a health check; if Buddy isn't
#     healthy in 60 seconds the branch on GitHub and the server go back to the commit before, and Buddy restarts.
set -euo pipefail
APP=${BUDDY_SELFDEPLOY_APP:-/opt/buddy}
LIB=/usr/local/lib/buddy-deploy
DIR=${BUDDY_SELFDEPLOY_DIR:-/var/lib/buddy/deploy}
FLAG=${BUDDY_SELFDEPLOY_FLAG:-/etc/buddy/selfdeploy-on}
SANDBOX_ENV=${BUDDY_SELFDEPLOY_SANDBOX_ENV:-/etc/buddy-sandbox/sandbox.env}
PROTECTED='^(deploy/|\.github/|backend/requirements|backend/pyproject|backend/tests/conftest\.py$|backend/buddy/(budgets|thrift|guard|privacy|config|selfcode|codejob|sandbox|selfdeploy|claudefix|access|compliance|usage)\.py$)'
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }

g() { git -c safe.directory="$1" -c core.hooksPath=/dev/null -c core.fsmonitor= -c credential.helper= -C "$1" "${@:2}"; }

install_units() {
  install -d -o root -g root -m 755 "$LIB"
  install -o root -g root -m 755 "$APP/deploy/selfdeploy.sh" "$LIB/selfdeploy.sh"
  install -d -o buddy -g buddy -m 770 "$DIR"
  cat > /etc/systemd/system/buddy-selfdeploy.path <<EOF
[Unit]
Description=Buddy self-deploy: a request from Buddy, after the owner's yes
[Path]
PathExists=$DIR/request.json
[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/buddy-selfdeploy.service <<EOF
[Unit]
Description=Buddy self-deploy: check, test, merge, deploy, roll back if unhealthy
[Service]
Type=oneshot
ExecStart=$LIB/selfdeploy.sh run
TimeoutStartSec=1800
EOF
  systemctl daemon-reload
  systemctl enable --now buddy-selfdeploy.path >/dev/null
}

result() {  # ok|refused|failed|rolled_back, message
  python3 - "$DIR/result.json" "$1" "$2" "${BRANCH:-}" <<'PY'
import json, sys, datetime
path, status, message, branch = sys.argv[1:5]
json.dump({"status": status, "message": message, "branch": branch,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(path, "w"))
PY
  chown buddy:buddy "$DIR/result.json"
  echo "$1: $2"
}

case "${1:-status}" in
  on)
    install_units; mkdir -p "$(dirname "$FLAG")"
    # The repo and branch to deploy are fixed now, by the owner, in a root-owned file: not read from the checkout later.
    printf 'REPO_URL=%s\nBASE=%s\n' "$(g "$APP" remote get-url origin)" "$(g "$APP" rev-parse --abbrev-ref HEAD)" > "$FLAG"
    chown root:root "$FLAG"; chmod 644 "$FLAG"
    grep -q '^SANDBOX_GITHUB_TOKEN=' "$SANDBOX_ENV" 2>/dev/null || \
      echo "  Note: merging needs the sandbox's GitHub token (Contents: read and write): sudo bash $APP/deploy/sandbox.sh token"
    echo "Self-deploy is on: after your yes, Buddy's own fixes are tested, merged and deployed (with a rollback)."
    exit 0 ;;
  off)
    rm -f "$FLAG"; systemctl disable --now buddy-selfdeploy.path >/dev/null 2>&1 || true
    echo "Self-deploy is off: Buddy's fixes stay on their branch for you to merge."; exit 0 ;;
  status)
    [[ -f $FLAG ]] && echo "on" || echo "off"; exit 0 ;;
  refresh)  # update.sh: keep the root-owned copy current, only while on
    [[ -f $FLAG ]] && install_units; exit 0 ;;
  run) ;;
  *) echo "Use: selfdeploy.sh [on|off|status]"; exit 1 ;;
esac

# --- run: one request -------------------------------------------------------------------------------------------
[[ -f $DIR/request.json ]] || exit 0
BRANCH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("branch",""))' "$DIR/request.json" || true)
rm -f "$DIR/request.json"
[[ -f $FLAG ]] || { result refused "Self-deploy is off (sudo bash $APP/deploy/selfdeploy.sh on)."; exit 0; }
[[ $BRANCH =~ ^buddy/[A-Za-z0-9._-]{1,80}$ ]] || { result refused "Not a buddy/... branch."; exit 0; }

# The checkout's own git settings must not run anything as root.
if grep -Eiq '^\s*(sshcommand|fsmonitor|hookspath|askpass|pager|editor|helper|external|textconv|command|smudge|clean|process|insteadof|pushinsteadof|gitproxy|proxy)\s*=|^\s*\[include' "$APP/.git/config"; then
  result refused "The server's git settings look changed; deploy by hand after checking $APP/.git/config."; exit 0
fi
REMOTE=$(sed -n 's/^REPO_URL=//p' "$FLAG"); BASE=$(sed -n 's/^BASE=//p' "$FLAG")
[[ $BASE =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$ ]] || { result refused "Turn self-deploy on again (selfdeploy.sh on)."; exit 0; }
[[ $REMOTE =~ github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?$ ]] || { result refused "The checkout isn't from GitHub."; exit 0; }
OWNER=${BASH_REMATCH[1]}; REPO=${BASH_REMATCH[2]}
g "$APP" fetch -q "$REMOTE" "+refs/heads/$BASE:refs/remotes/origin/$BASE" "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
PREV=$(g "$APP" rev-parse "origin/$BASE")

CHANGED=$(g "$APP" diff --name-only "origin/$BASE...origin/$BRANCH")
[[ -n $CHANGED ]] || { result refused "The branch has no changes."; exit 0; }
if BAD=$(grep -E "$PROTECTED" <<<"$CHANGED"); then
  result refused "It changes Buddy's guard rails ($(echo $BAD | head -c 200)), which are never self-deployed. Review and merge it on GitHub yourself."
  exit 0
fi
if g "$APP" diff --name-only --diff-filter=D "origin/$BASE...origin/$BRANCH" | grep -q '^backend/tests/'; then
  result refused "It deletes tests, which is never self-deployed. Review it on GitHub."; exit 0
fi

# Test the merged code, as the buddy user, with no secrets.
WORK=$(mktemp -d /var/tmp/buddy-selfdeploy.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
git init -q "$WORK/repo"
g "$WORK/repo" fetch -q "$APP" "refs/remotes/origin/$BASE:refs/heads/base" "refs/remotes/origin/$BRANCH:refs/heads/fix"
g "$WORK/repo" checkout -q base
if ! g "$WORK/repo" -c user.name=Buddy -c user.email=buddy@localhost merge -q --no-edit fix; then
  result refused "It doesn't merge cleanly with the current code."; exit 0
fi
chown -R buddy:buddy "$WORK"
if ! (cd "$WORK/repo/backend" && timeout 900 sudo -u buddy env -i PATH=/usr/bin:/bin HOME="$WORK" \
      "$APP/backend/.venv/bin/python" -m pytest -q -p no:cacheprovider tests/ > "$WORK/tests.out" 2>&1); then
  result refused "The tests failed, so nothing was merged: $(tail -3 "$WORK/tests.out" | tr '\n' ' ' | head -c 300)"
  exit 0
fi

# Merge on GitHub.
TOKEN=$(grep '^SANDBOX_GITHUB_TOKEN=' "$SANDBOX_ENV" 2>/dev/null | cut -d= -f2- || true)
[[ -n $TOKEN ]] || { result refused "No GitHub token to merge with (sudo bash $APP/deploy/sandbox.sh token)."; exit 0; }
api() { curl -sS -o "$WORK/api.out" -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" "$@"; }
CODE=$(api -X POST "https://api.github.com/repos/$OWNER/$REPO/merges" \
  -d "{\"base\":\"$BASE\",\"head\":\"$BRANCH\",\"commit_message\":\"Merge $BRANCH (Buddy's fix, approved by the owner, tests passed)\"}")
[[ $CODE == 201 || $CODE == 204 ]] || { result refused "GitHub didn't merge it (HTTP $CODE)."; exit 0; }

# Deploy exactly what GitHub now has (no local edits survive), then check Buddy is healthy; roll back if not.
g "$APP" fetch -q "$REMOTE" "+refs/heads/$BASE:refs/remotes/origin/$BASE"
g "$APP" checkout -q "$BASE" && g "$APP" reset -q --hard "origin/$BASE"
if BUDDY_UPDATE_REEXEC=1 bash "$APP/deploy/update.sh" > "$WORK/update.out" 2>&1 && curl -fsS http://127.0.0.1:8000/health >/dev/null; then
  result ok "Merged and deployed $BRANCH: tests passed and Buddy is healthy. Previous version: ${PREV:0:7}."
  exit 0
fi
NEW=$(g "$APP" rev-parse HEAD)
api -X PATCH "https://api.github.com/repos/$OWNER/$REPO/git/refs/heads/$BASE" -d "{\"sha\":\"$PREV\",\"force\":true}" >/dev/null || true
g "$APP" reset -q --hard "$PREV" && chown -R buddy:buddy "$APP" && systemctl restart buddy
sleep 10
result rolled_back "Deployed $BRANCH but Buddy wasn't healthy, so it went back to ${PREV:0:7} (here and on GitHub; the bad merge was ${NEW:0:7}). The branch is still on GitHub to look at."
