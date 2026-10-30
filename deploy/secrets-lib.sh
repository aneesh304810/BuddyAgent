# Shared by the deploy scripts that read or change Buddy's settings. Source it, then:
#   secrets_open     sets ENV_FILE to a file you can read and edit
#   secrets_seal     saves your changes (call it before restarting Buddy); also runs on exit
#
# Two modes:
#   plain   /etc/buddy/buddy.env, root only (600). ENV_FILE is that file.
#   sealed  /etc/buddy/buddy.env.cred, encrypted with systemd-creds (this machine's own key,
#           root only). There's no plain copy on disk: ENV_FILE is a temporary decrypted copy
#           in /run (memory, root only) that's encrypted back and shredded when you're done.
#           Only the Buddy services can decrypt it, into memory, while they run.
# Switch with: sudo bash /opt/buddy/deploy/security.sh seal | unseal
#
# Either way, every change is dated in /etc/buddy/key-dates, so the weekly check can remind
# you to rotate old keys.

PLAIN_ENV=/etc/buddy/buddy.env
SEALED_ENV=/etc/buddy/buddy.env.cred
KEY_DATES=/etc/buddy/key-dates
_SECRETS_TMP=""
_SECRETS_BEFORE=""

secrets_sealed() { [[ -f $SEALED_ENV ]]; }

secrets_open() {
  umask 077
  _SECRETS_BEFORE=$(mktemp -p /run 2>/dev/null || mktemp)
  if secrets_sealed; then
    install -d -m 700 /run/buddy-secrets
    _SECRETS_TMP=$(mktemp -p /run/buddy-secrets)
    systemd-creds decrypt --name=buddy-env "$SEALED_ENV" "$_SECRETS_TMP"
    ENV_FILE=$_SECRETS_TMP
  else
    ENV_FILE=$PLAIN_ENV
  fi
  [[ -f $ENV_FILE ]] && cp "$ENV_FILE" "$_SECRETS_BEFORE"
  trap secrets_close EXIT
}

_secrets_date_changes() {
  # Record today's date for every setting whose value changed (names only, never values).
  [[ -f $ENV_FILE ]] || return 0
  python3 - "$_SECRETS_BEFORE" "$ENV_FILE" "$KEY_DATES" <<'PY'
import datetime, os, sys
def read(path):
    try:
        return dict(l.split("=", 1) for l in open(path).read().splitlines() if "=" in l and not l.startswith("#"))
    except FileNotFoundError:
        return {}
before, after, dates_path = read(sys.argv[1]), read(sys.argv[2]), sys.argv[3]
dates = read(dates_path)
today = datetime.date.today().isoformat()
for name, value in after.items():
    if before.get(name) != value:
        dates[name] = today            # new or changed now
    elif name not in dates:
        dates[name] = "unknown"        # set before dates were kept
for name in list(dates):
    if name not in after:
        del dates[name]
with open(dates_path, "w") as f:
    f.write("".join(f"{k}={v}\n" for k, v in sorted(dates.items())))
os.chmod(dates_path, 0o600)
PY
}

secrets_seal() {
  [[ -n $_SECRETS_BEFORE ]] || return 0
  _secrets_date_changes || true
  if [[ -n $_SECRETS_TMP && -f $_SECRETS_TMP ]]; then
    if ! cmp -s "$_SECRETS_TMP" "$_SECRETS_BEFORE"; then
      systemd-creds encrypt --name=buddy-env "$_SECRETS_TMP" "$SEALED_ENV.new"
      chmod 600 "$SEALED_ENV.new" && mv "$SEALED_ENV.new" "$SEALED_ENV"
      cp "$_SECRETS_TMP" "$_SECRETS_BEFORE"
    fi
  elif [[ -f $PLAIN_ENV ]]; then
    chown root:root "$PLAIN_ENV" && chmod 600 "$PLAIN_ENV"
  fi
}

secrets_close() {
  secrets_seal
  [[ -n $_SECRETS_TMP ]] && { shred -u "$_SECRETS_TMP" 2>/dev/null || rm -f "$_SECRETS_TMP"; }
  [[ -n $_SECRETS_BEFORE ]] && { shred -u "$_SECRETS_BEFORE" 2>/dev/null || rm -f "$_SECRETS_BEFORE"; }
  _SECRETS_TMP="" _SECRETS_BEFORE=""
  trap - EXIT
}

# Read one setting's value (for scripts that need, say, the access token).
secrets_get() {
  local name=$1
  grep "^$name=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-
}
