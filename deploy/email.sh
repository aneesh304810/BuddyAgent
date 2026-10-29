#!/usr/bin/env bash
# Add your email accounts to Buddy in one go:
#   sudo bash /opt/buddy/deploy/email.sh
# For each account it asks for the address and its app password (hidden), checks
# the login with the mail server, and saves it. Accounts already set up are shown
# and kept unless you replace them. Buddy restarts once at the end.
set -euo pipefail
ENV_FILE=/etc/buddy/buddy.env
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)."; exit 1; }

# The Python program is passed with -c so the keyboard stays its input.
SCRIPT=$(cat <<'PY'
import getpass, imaplib, re, socket, sys

path = sys.argv[1]
HOSTS = {"gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com", "yahoo.com": "imap.mail.yahoo.com",
         "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com", "mac.com": "imap.mail.me.com"}
MICROSOFT = ("outlook.com", "hotmail.com", "live.com", "msn.com")


def key(n, what):
    return f"BUDDY_EMAIL_{what}" if n == 1 else f"BUDDY_EMAIL_{n}_{what}"


def read():
    values = {}
    for line in open(path).read().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


def save(updates):
    text = open(path).read()
    for k, v in updates.items():
        line = f"{k}={v}"
        if re.search(rf"(?m)^{k}=", text):
            text = re.sub(rf"(?m)^{k}=.*$", lambda m: line, text)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"
    open(path, "w").write(text)


def check(address, password):
    domain = address.rsplit("@", 1)[-1].lower()
    host = HOSTS.get(domain, "imap.gmail.com")
    try:
        imap = imaplib.IMAP4_SSL(host, timeout=30)
        imap.login(address, password)
        imap.logout()
        return None
    except imaplib.IMAP4.error:
        return "the login was refused: check the app password (and that 2-Step Verification is on)"
    except (OSError, socket.timeout) as e:
        return f"couldn't reach {host} ({type(e).__name__})"


values = read()
existing = {n: values.get(key(n, "ADDRESS"), "") for n in range(1, 10)}
print("\nEmail accounts Buddy has now:")
shown = False
for n, address in existing.items():
    if address and values.get(key(n, "APP_PASSWORD")):
        print(f"  {n}. {address}")
        shown = True
if not shown:
    print("  none yet")

print("\nAdd accounts one by one. Press Enter on an empty address when you're done.")
added = 0
while True:
    address = input("\nEmail address: ").strip()
    if not address:
        break
    if "@" not in address:
        print("  That doesn't look like an email address.")
        continue
    if address.rsplit("@", 1)[-1].lower() in MICROSOFT:
        print("  Outlook and Hotmail no longer allow app passwords. Buddy reads those through")
        print("  phone notifications instead (Buddy app -> Settings -> notification access).")
        continue
    password = getpass.getpass("App password (16 letters, hidden): ").replace(" ", "").strip()
    if not password:
        print("  Skipped: no password.")
        continue
    print("  Checking the login...")
    problem = check(address, password)
    if problem:
        print(f"  Not saved: {problem}.")
        continue
    slot = next((n for n, a in existing.items() if a.lower() == address.lower()), None) \
        or next((n for n, a in existing.items() if not a), None)
    if slot is None:
        print("  Not saved: Buddy holds up to 9 accounts.")
        continue
    save({key(slot, "ADDRESS"): address, key(slot, "APP_PASSWORD"): password})
    existing[slot] = address
    added += 1
    print(f"  Saved as account {slot}: {address}")

if added:
    print(f"\n{added} account(s) saved.")
    sys.exit(0)
print("\nNothing changed.")
sys.exit(3)
PY
)
status=0
python3 -c "$SCRIPT" "$ENV_FILE" || status=$?
if [[ $status -eq 0 ]]; then
  systemctl restart buddy
  echo "Buddy restarted. Ask it: \"Which email accounts do you have?\""
fi
