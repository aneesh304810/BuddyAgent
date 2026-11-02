#!/usr/bin/env python3
"""Who and what can reach Buddy's server and its data: review, approve, revoke. Root only.

    sudo bash /opt/buddy/deploy/access.sh review          quarterly access review (records evidence)
    sudo bash /opt/buddy/deploy/access.sh list            what has access right now, against the approved list
    sudo bash /opt/buddy/deploy/access.sh revoke user NAME       lock an account: password, shell, sudo, SSH keys, sessions
    sudo bash /opt/buddy/deploy/access.sh revoke key FINGERPRINT remove one SSH key everywhere
    sudo bash /opt/buddy/deploy/access.sh revoke device NAME     remove a device from the tailnet (needs BUDDY_TAILSCALE_API_KEY)
    sudo bash /opt/buddy/deploy/access.sh offboard NAME   everything for a person leaving: their account, keys and
                                                          devices, then a new app token and the keys to rotate
    sudo bash /opt/buddy/deploy/access.sh enforce         what the daily timer runs: lock unapproved accounts and keys

The approved list is /etc/buddy/access-approved.json (written by a review). Evidence of every review
and revoke goes to /var/lib/buddy/access-reviews/ and /var/log/buddy-access.log. Policy:
docs/ACCESS_CONTROL.md. Never prints or stores a secret value.
"""

import datetime
import json
import os
import pwd
import grp
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

APPROVED = Path("/etc/buddy/access-approved.json")
REVIEWS = Path("/var/lib/buddy/access-reviews")
LOG = Path("/var/log/buddy-access.log")
KEY_DATES = Path("/etc/buddy/key-dates")
SUDOERS_D = Path("/etc/sudoers.d")
REVIEW_DAYS = 90          # periodic review: every quarter
STALE_DEVICE_DAYS = 60    # a tailnet device not seen this long should be removed
ADMIN_GROUPS = ("sudo", "admin", "wheel")
NO_LOGIN = ("nologin", "false", "sync", "halt", "shutdown")
# System accounts Buddy's own setup makes; never human, never locked here.
SERVICE_ACCOUNTS = {"buddy", "buddy-sandbox"}

# Things only you can check (they live in other companies' consoles); a review records your answer.
MANUAL_CHECKS = [
    ("plaid_team", "Plaid dashboard: Team settings list only you, and two-step login is on"),
    ("plaid_keys", "Plaid dashboard: the API keys are only in Buddy's settings (rotate the secret if unsure)"),
    ("google_account", "Google account (your sign-in for Tailscale and Plaid): two-step or passkey on; no unknown devices"),
    ("tailscale_admin", "Tailscale admin console: only you are a user/admin; device approval and key expiry are on"),
    ("github", "GitHub: no collaborators on the Buddy repo you don't expect; tokens have expiry dates"),
    ("digitalocean", "DigitalOcean: only you on the team; two-step login on"),
    ("phones", "Phones/browsers holding the Buddy app link are all yours (rotate the token if one was lost)"),
]


def sh(*args, timeout: int = 60, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input)  # nosec B603


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def log(line: str) -> None:
    try:
        with LOG.open("a") as f:
            f.write(f"{now():%Y-%m-%d %H:%M} {line}\n")
    except OSError:
        pass


# --- what has access ----------------------------------------------------------------
def login_users() -> list[dict]:
    """Accounts that can log in: root and humans (uid >= 1000) with a real shell."""
    admins = set()
    for g in ADMIN_GROUPS:
        try:
            admins.update(grp.getgrnam(g).gr_mem)
        except KeyError:
            pass
    out = []
    for p in pwd.getpwall():
        human = p.pw_uid == 0 or (1000 <= p.pw_uid < 65534)
        if not human or p.pw_name in SERVICE_ACCOUNTS or p.pw_shell.rsplit("/", 1)[-1] in NO_LOGIN:
            continue
        status = sh("passwd", "-S", p.pw_name).stdout.split()
        out.append({"name": p.pw_name, "uid": p.pw_uid, "home": p.pw_dir, "shell": p.pw_shell,
                    "admin": p.pw_uid == 0 or p.pw_name in admins,
                    "password": {"P": "set", "L": "locked", "NP": "none"}.get(status[1] if len(status) > 1 else "", "?")})
    return out


def ssh_keys(users: list[dict]) -> list[dict]:
    keys = []
    for u in users:
        path = Path(u["home"]) / ".ssh" / "authorized_keys"
        if not path.exists():
            continue
        for line in sh("ssh-keygen", "-lf", str(path)).stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                keys.append({"user": u["name"], "fingerprint": parts[1], "comment": " ".join(parts[2:-1])[:60]})
    return keys


def sudoers() -> list[str]:
    try:
        return sorted(p.name for p in SUDOERS_D.iterdir() if p.is_file() and not p.name.startswith(".") and p.name != "README")
    except OSError:
        return []


def tailnet() -> dict:
    """Devices and users on the tailnet, from tailscale status (no keys, just names and dates)."""
    if not shutil.which("tailscale"):
        return {"devices": [], "users": []}
    try:
        data = json.loads(sh("tailscale", "status", "--json").stdout or "{}")
    except json.JSONDecodeError:
        return {"devices": [], "users": []}
    users = {str(k): v.get("LoginName", "") for k, v in (data.get("User") or {}).items()}
    devices = []
    for node in [data.get("Self") or {}] + list((data.get("Peer") or {}).values()):
        if not node:
            continue
        devices.append({"name": node.get("HostName", ""), "dns": (node.get("DNSName") or "").rstrip("."),
                        "id": node.get("ID", ""), "user": users.get(str(node.get("UserID")), ""),
                        "os": node.get("OS", ""), "online": bool(node.get("Online")),
                        "last_seen": node.get("LastSeen", ""), "tags": node.get("Tags") or [],
                        "key_expiry": node.get("KeyExpiry", ""), "self": node is data.get("Self")})
    return {"devices": devices, "users": sorted({d["user"] for d in devices if d["user"]})}


def key_ages() -> dict:
    try:
        return dict(l.split("=", 1) for l in KEY_DATES.read_text().splitlines() if "=" in l)
    except OSError:
        return {}


def inventory() -> dict:
    users = login_users()
    net = tailnet()
    return {"at": now().isoformat(), "users": users, "ssh_keys": ssh_keys(users), "sudoers": sudoers(),
            "devices": net["devices"], "tailnet_users": net["users"], "key_dates": key_ages()}


# --- against the approved list ----------------------------------------------------------
def approved() -> dict:
    try:
        return json.loads(APPROVED.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def baseline(inv: dict) -> dict:
    return {"users": sorted(u["name"] for u in inv["users"]),
            "admins": sorted(u["name"] for u in inv["users"] if u["admin"]),
            "ssh_keys": sorted(k["fingerprint"] for k in inv["ssh_keys"]),
            "sudoers": inv["sudoers"],
            "devices": sorted(d["name"] for d in inv["devices"]),
            "tailnet_users": inv["tailnet_users"]}


def _days_since(stamp: str) -> int | None:
    try:
        when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if when.year < 2000:  # tailscale's "never"
        return None
    return (now() - when).days


def differences(inv: dict, ok: dict) -> list[dict]:
    """Everything that has access but isn't on the approved list, plus stale devices."""
    if not ok:
        return []
    out = []
    current = baseline(inv)
    for kind, what in (("users", "account"), ("admins", "admin (sudo) account"), ("ssh_keys", "SSH key"),
                       ("sudoers", "sudoers file"), ("devices", "tailnet device"), ("tailnet_users", "tailnet user")):
        for item in sorted(set(current[kind]) - set(ok.get(kind, []))):
            out.append({"kind": kind, "item": item, "what": f"Unapproved {what}: {item}"})
    for d in inv["devices"]:
        days = _days_since(d.get("last_seen", ""))
        if not d.get("self") and not d.get("online") and days is not None and days > STALE_DEVICE_DAYS:
            out.append({"kind": "stale_device", "item": d["name"],
                        "what": f"Tailnet device {d['name']} not seen for {days} days"})
    return out


def last_review() -> dict | None:
    try:
        files = sorted(REVIEWS.glob("review-*.json"))
    except OSError:
        return None
    for path in reversed(files):
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None


def review_due() -> int | None:
    """Days overdue (0 or more) when a review is due; None when it isn't."""
    last = last_review()
    if not last:
        return 0
    days = _days_since(last.get("at", ""))
    return None if days is not None and days < REVIEW_DAYS else max(0, (days or REVIEW_DAYS) - REVIEW_DAYS)


def check(find) -> None:
    """For the weekly security check (maintain.py): findings, never secrets."""
    ok = approved()
    if not ok:
        find("act", "No access review on record", "Plaid asks for periodic access reviews.",
             "Run the first one (about 5 minutes): sudo bash /opt/buddy/deploy/access.sh review")
        return
    due = review_due()
    if due is not None:
        find("watch", "The quarterly access review is due", f"Last one: {(last_review() or {}).get('at', 'never')[:10]}.",
             "sudo bash /opt/buddy/deploy/access.sh review")
    for d in differences(inventory(), ok):
        tool = "sudo bash /opt/buddy/deploy/access.sh"
        fix = {"devices": f"If it's yours: {tool} review. If not: {tool} revoke device {d['item']}",
               "stale_device": f"{tool} revoke device {d['item']}",
               "tailnet_users": f"Remove them in the Tailscale admin console (Users), then {tool} review",
               }.get(d["kind"], f"Removed automatically each day; if it's expected, approve it with {tool} review")
        find("watch" if d["kind"] == "stale_device" else "act", d["what"], "Not on the approved access list.", fix)


# --- revoke -----------------------------------------------------------------------------
def revoke_user(name: str, reason: str = "revoked") -> list[str]:
    try:
        p = pwd.getpwnam(name)
    except KeyError:
        return [f"No account named {name}."]
    if p.pw_uid == 0 or name in SERVICE_ACCOUNTS:
        return [f"{name} can't be revoked here (root and Buddy's own service accounts are kept)."]
    steps = []
    for args, what in ((("usermod", "--lock", "--expiredate", "1", "--shell", "/usr/sbin/nologin", name),
                        "password locked, account expired, shell removed"),):
        r = sh(*args)
        steps.append(what if r.returncode == 0 else f"{what}: failed ({r.stderr.strip()[:80]})")
    for g in ADMIN_GROUPS:
        try:
            if name in grp.getgrnam(g).gr_mem and sh("gpasswd", "-d", name, g).returncode == 0:
                steps.append(f"removed from {g}")
        except KeyError:
            pass
    for f in (SUDOERS_D / name, SUDOERS_D / f"90-{name}"):
        if f.exists():
            f.unlink()
            steps.append(f"removed {f}")
    keys = Path(p.pw_dir) / ".ssh" / "authorized_keys"
    if keys.exists():
        REVIEWS.mkdir(parents=True, exist_ok=True)
        keep = REVIEWS / f"revoked-keys-{name}-{now():%Y%m%d%H%M}"
        shutil.move(str(keys), keep)
        os.chmod(keep, 0o600)
        steps.append("SSH keys removed (kept as evidence, root only)")
    if sh("pkill", "-KILL", "-u", name).returncode == 0:
        steps.append("open sessions ended")
    _drop_from_approved("users", name)
    _drop_from_approved("admins", name)
    log(f"{reason} user {name}: {'; '.join(steps)}")
    return steps


def revoke_key(fingerprint: str) -> list[str]:
    steps = []
    for u in login_users():
        path = Path(u["home"]) / ".ssh" / "authorized_keys"
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        keep = []
        for line in lines:
            fp = ""
            if line.strip() and not line.lstrip().startswith("#"):
                parts = sh("ssh-keygen", "-lf", "-", input=line).stdout.split()
                fp = parts[1] if len(parts) > 1 else ""
            if fp == fingerprint:
                steps.append(f"removed from {u['name']}'s keys")
            else:
                keep.append(line)
        if len(keep) != len(lines):
            path.write_text("\n".join(keep) + ("\n" if keep else ""))
    _drop_from_approved("ssh_keys", fingerprint)
    log(f"revoked key {fingerprint}: {'; '.join(steps) or 'not found'}")
    return steps or [f"No SSH key with fingerprint {fingerprint}."]


def tailscale_api_key() -> str:
    key = os.environ.get("BUDDY_TAILSCALE_API_KEY", "")
    if key:
        return key
    try:  # read from Buddy's settings (sealed or plain); never printed
        here = Path(__file__).resolve().parent
        sys.path.insert(0, str(here))
        import maintain  # noqa: PLC0415
        return maintain.settings().get("BUDDY_TAILSCALE_API_KEY", "")
    except Exception:
        return ""


def revoke_device(name: str, opener=None) -> list[str]:
    opener = opener or urllib.request.urlopen
    dev = next((d for d in tailnet()["devices"] if name in (d["name"], d["dns"], d["dns"].split(".")[0])), None)
    if not dev:
        return [f"No tailnet device named {name}."]
    if dev.get("self"):
        return ["That's this server; remove it in the Tailscale admin console if you really mean to."]
    key = tailscale_api_key()
    if not key or not dev.get("id"):
        log(f"device {name}: manual removal needed (no Tailscale API key)")
        return [f"Remove {dev['name']} at https://login.tailscale.com/admin/machines (… → Remove). "
                "To do this automatically, save an API key: sudo bash /opt/buddy/deploy/set.sh BUDDY_TAILSCALE_API_KEY"]
    err = ""
    req = urllib.request.Request(f"https://api.tailscale.com/api/v2/device/{dev['id']}", method="DELETE",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with opener(req, timeout=20) as r:  # nosec B310 (fixed https host)
            ok = 200 <= r.status < 300
    except Exception as e:  # report, don't crash an offboarding halfway
        ok, err = False, type(e).__name__
    _drop_from_approved("devices", dev["name"]) if ok else None
    log(f"revoked device {dev['name']}: {'removed from tailnet' if ok else 'failed'}")
    return [f"{dev['name']} removed from the tailnet." if ok else f"Couldn't remove {dev['name']} ({err})."]


def offboard(name: str) -> list[str]:
    """A person leaving or changing role: everything they could use, in one go."""
    steps = []
    if name in {u["name"] for u in login_users()}:
        steps += revoke_user(name, reason="offboarded")
    for k in [k for k in ssh_keys(login_users()) if name.lower() in k["comment"].lower()]:
        steps += revoke_key(k["fingerprint"])
    for d in [d for d in tailnet()["devices"] if d["user"].split("@")[0].lower() == name.lower()
              or d["user"].lower() == name.lower()]:
        steps += revoke_device(d["name"])
    steps.append("Next: a new app token (the script does this now), then rotate any key they saw: "
                 "Plaid secret, Claude key, Gmail app passwords (set.sh NAME), and remove them from the "
                 "Plaid, Tailscale, GitHub and DigitalOcean teams.")
    log(f"offboarded {name}")
    return steps


def enforce() -> list[str]:
    """Daily: lock accounts and remove SSH keys that aren't on the approved list (automatic de-provisioning)."""
    if os.environ.get("BUDDY_ACCESS_AUTO_REVOKE", "1") == "0":
        return []
    ok = approved()
    if not ok:
        return []
    done = []
    inv = inventory()
    for d in differences(inv, ok):
        if d["kind"] == "users":
            done += [f"{d['item']}: {s}" for s in revoke_user(d["item"], reason="auto-revoked (not approved)")]
        elif d["kind"] == "ssh_keys":
            done += revoke_key(d["item"])
    return done


def _drop_from_approved(kind: str, item: str) -> None:
    ok = approved()
    if ok and item in ok.get(kind, []):
        ok[kind] = [x for x in ok[kind] if x != item]
        _write_approved(ok)


def _write_approved(ok: dict) -> None:
    APPROVED.parent.mkdir(parents=True, exist_ok=True)
    APPROVED.write_text(json.dumps(ok, indent=2))
    os.chmod(APPROVED, 0o600)


# --- review -----------------------------------------------------------------------------
def describe(inv: dict, diffs: list[dict]) -> str:
    lines = ["Accounts that can log in:"]
    lines += [f"  {u['name']}{' (admin)' if u['admin'] else ''}, password {u['password']}" for u in inv["users"]]
    lines.append(f"SSH keys: {len(inv['ssh_keys'])}")
    lines += [f"  {k['user']}: {k['fingerprint']} {k['comment']}" for k in inv["ssh_keys"]]
    lines.append(f"Sudoers files: {', '.join(inv['sudoers']) or 'none'}")
    lines.append(f"Tailnet users: {', '.join(inv['tailnet_users']) or 'none'}")
    lines.append("Tailnet devices:")
    for d in inv["devices"]:
        seen = "online" if d["online"] or d["self"] else f"last seen {d['last_seen'][:10] or '?'}"
        lines.append(f"  {d['name']} ({d['os']}, {d['user'] or 'tagged'}), {seen}{' — this server' if d['self'] else ''}")
    lines.append("Key ages (names only): " + ", ".join(f"{k} {v}" for k, v in sorted(inv["key_dates"].items())))
    if diffs:
        lines.append("\nNot on the approved list:")
        lines += [f"  ! {d['what']}" for d in diffs]
    return "\n".join(lines)


def review(ask=input, reviewer: str = "") -> dict:
    inv = inventory()
    diffs = differences(inv, approved())
    print(describe(inv, diffs))
    print("\nAnswer y (yes, checked) or n (no / needs fixing) for each:")
    answers = {}
    for key, question in MANUAL_CHECKS:
        answers[key] = ask(f"  {question}? [y/n] ").strip().lower().startswith("y")
    revoked = []
    for d in diffs:
        if d["kind"] in ("users", "ssh_keys", "devices", "stale_device") and \
                ask(f"  Revoke {d['item']} ({d['what']})? [y/n] ").strip().lower().startswith("y"):
            revoked += {"users": revoke_user, "ssh_keys": revoke_key}.get(d["kind"], revoke_device)(d["item"])
    if revoked:
        inv = inventory()
    reviewer = reviewer or ask("Your name, for the record: ").strip() or "owner"
    approve = ask("Is everything still listed needed, and is it the approved access list now? [y/n] ").strip().lower().startswith("y")
    record = {"at": now().isoformat(), "reviewer": reviewer, "approved": approve, "inventory": inv,
              "not_approved_before": diffs, "revoked": revoked, "checks": answers,
              "follow_up": [q for k, q in MANUAL_CHECKS if not answers[k]]}
    REVIEWS.mkdir(parents=True, exist_ok=True)
    path = REVIEWS / f"review-{now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps(record, indent=2))
    os.chmod(path, 0o600)
    if approve:
        _write_approved({**baseline(inv), "approved_at": record["at"], "approved_by": reviewer})
    log(f"access review by {reviewer}: {'approved' if approve else 'NOT approved'}; "
        f"{len(diffs)} differences, {len(revoked)} revoke steps, {len(record['follow_up'])} follow-ups")
    print(f"\nSaved: {path}")
    if record["follow_up"]:
        print("Fix these, then run the review again:\n  " + "\n  ".join(record["follow_up"]))
    print(f"Next review due by {(now() + datetime.timedelta(days=REVIEW_DAYS)):%d %b %Y}.")
    return record


def main(argv: list[str]) -> int:
    if os.geteuid() != 0:
        print("Run as root (sudo).")
        return 1
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "review":
        review()
    elif cmd == "list":
        inv = inventory()
        print(describe(inv, differences(inv, approved())))
        due = review_due()
        print("\nAccess review: " + ("due now" if due is not None else f"done {(last_review() or {}).get('at', '')[:10]}"))
    elif cmd == "revoke" and len(argv) == 4 and argv[2] in ("user", "key", "device"):
        print("\n".join({"user": revoke_user, "key": revoke_key, "device": revoke_device}[argv[2]](argv[3])))
    elif cmd == "offboard" and len(argv) == 3:
        print("\n".join(offboard(argv[2])))
    elif cmd == "enforce":
        print("\n".join(enforce()) or "Nothing to revoke.")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
