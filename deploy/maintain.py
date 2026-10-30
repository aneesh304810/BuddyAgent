#!/usr/bin/env python3
"""Weekly patching and security check for Buddy's server. Runs as root from a systemd timer
(buddy-maintain.timer, Sundays around 4am) or by hand: sudo bash /opt/buddy/deploy/security.sh check

Patches:
  - Ubuntu security updates (unattended-upgrades also runs daily) and Tailscale and Docker
  - Buddy's Python packages with known vulnerabilities, upgraded to the fixed version; if Buddy
    doesn't come back healthy, the old versions are put back
  - the code sandbox image, rebuilt on the newest base image

Checks (each finding says what to do):
  - keys: encrypted at rest or not, file permissions, how old each key is, access token strength
  - leaked keys in the last week's logs
  - anything listening outside localhost and the tailnet; firewall; SSH passwords; Tailscale Funnel
  - automatic updates on, reboot pending, backups recent, disk space
  - how locked down Buddy's service is (systemd-analyze security)

Writes /var/lib/buddy/security.json, which Buddy's daily health check and digest read, and
appends a line to /var/log/buddy-maintain.log. Never prints a key.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP = Path(os.getenv("BUDDY_APP", "/opt/buddy"))
VENV = APP / "backend" / ".venv"
TOOLS = Path("/var/lib/buddy-tools")
REPORT = Path("/var/lib/buddy/security.json")
LOG = Path("/var/log/buddy-maintain.log")
PLAIN_ENV, SEALED_ENV, KEY_DATES = Path("/etc/buddy/buddy.env"), Path("/etc/buddy/buddy.env.cred"), Path("/etc/buddy/key-dates")
ROTATE_DAYS = 90
NO_PATCH = "--check-only" in sys.argv

findings: list[dict] = []
done: list[str] = []


def find(severity: str, title: str, detail: str, fix: str) -> None:
    findings.append({"severity": severity, "area": "security", "title": title, "detail": detail, "fix": fix})


def sh(*args, timeout: int = 900, check: bool = False, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=check, **kw)


def healthy(wait: int = 40) -> bool:
    for _ in range(wait):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as r:  # nosec B310 (local)
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


# --- patches ------------------------------------------------------------------------
def patch_os() -> None:
    sh("apt-get", "update", "-q", timeout=600)
    if shutil.which("unattended-upgrade"):
        sh("unattended-upgrade", timeout=3000)
    extra = [p for p in ("tailscale", "docker.io", "docker-ce") if sh("dpkg", "-s", p).returncode == 0]
    if extra:
        sh("apt-get", "install", "-y", "-q", "--only-upgrade", *extra, timeout=1200,
           env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    done.append("Ubuntu security updates, " + (", ".join(extra) or "no extras"))


def audit_python() -> list[dict]:
    """Known vulnerabilities in Buddy's installed packages (pip-audit, in its own tools venv)."""
    if not (TOOLS / "bin" / "pip-audit").exists():
        sh(sys.executable, "-m", "venv", str(TOOLS), check=True)
        sh(str(TOOLS / "bin" / "pip"), "install", "-q", "--upgrade", "pip", "pip-audit", timeout=600, check=True)
    else:
        sh(str(TOOLS / "bin" / "pip"), "install", "-q", "--upgrade", "pip-audit", timeout=600)
    freeze = sh(str(VENV / "bin" / "pip"), "freeze", "--exclude-editable").stdout
    req = TOOLS / "buddy-freeze.txt"
    req.write_text(freeze)
    out = sh(str(TOOLS / "bin" / "pip-audit"), "-r", str(req), "--no-deps", "--disable-pip", "-f", "json",
             timeout=600).stdout
    try:
        deps = json.loads(out).get("dependencies", [])
    except json.JSONDecodeError:
        find("watch", "The Python vulnerability scan didn't run", out[-200:] or "no output",
             "Run sudo bash /opt/buddy/deploy/security.sh check and look at the output.")
        return []
    return [{"name": d["name"], "version": d["version"], "id": v["id"], "fix": v.get("fix_versions", [])}
            for d in deps for v in d.get("vulns", [])]


def patch_python(vulns: list[dict]) -> None:
    fixable = {}
    for v in vulns:
        if v["fix"]:
            fixable[v["name"]] = max([fixable.get(v["name"], "0")] + v["fix"], key=_version_key)
    if not fixable:
        return
    before = sh(str(VENV / "bin" / "pip"), "freeze", "--exclude-editable").stdout
    backup = TOOLS / f"rollback-{datetime.date.today()}.txt"
    backup.write_text(before)
    specs = [f"{name}>={version}" for name, version in fixable.items()]
    result = sh("sudo", "-u", "buddy", str(VENV / "bin" / "pip"), "install", "-q", "--upgrade", *specs, timeout=1200)
    sh("systemctl", "restart", "buddy")
    if result.returncode == 0 and healthy():
        done.append("Python packages patched: " + ", ".join(specs))
        return
    # Put the old versions back so Buddy keeps working; report it for a manual look.
    sh("sudo", "-u", "buddy", str(VENV / "bin" / "pip"), "install", "-q", "-r", str(backup), timeout=1200)
    sh("systemctl", "restart", "buddy")
    find("act", "A security patch for Buddy's packages broke it, so it was rolled back",
         ", ".join(specs), "Run sudo bash /opt/buddy/deploy/update.sh and send the error it shows.")


def _version_key(v: str):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.+-]", v)]


def patch_sandbox() -> None:
    if not Path("/etc/systemd/system/buddy-sandbox.service").exists():
        return
    r = sh("docker", "build", "-q", "--pull", "-t", "buddy-sandbox:latest", str(APP / "deploy" / "sandbox"), timeout=1800)
    if r.returncode == 0:
        sh("docker", "image", "prune", "-f")
        done.append("sandbox image rebuilt on the newest base")
    else:
        find("watch", "The sandbox image didn't rebuild", r.stderr[-200:], "Run sudo bash /opt/buddy/deploy/sandbox.sh")


# --- checks -------------------------------------------------------------------------
def settings() -> dict:
    """Setting names and values, only to check them here (never printed or saved)."""
    text = ""
    if SEALED_ENV.exists():
        text = sh("systemd-creds", "decrypt", "--name=buddy-env", str(SEALED_ENV), "-").stdout
    elif PLAIN_ENV.exists():
        text = PLAIN_ENV.read_text()
    return dict(l.split("=", 1) for l in text.splitlines() if "=" in l and not l.startswith("#"))


def check_secrets(env: dict) -> None:
    if PLAIN_ENV.exists() and not SEALED_ENV.exists():
        find("watch", "Your keys are stored unencrypted on disk", "/etc/buddy/buddy.env is root-only but plain text.",
             "Encrypt them: sudo bash /opt/buddy/deploy/security.sh seal")
    for path in (PLAIN_ENV, SEALED_ENV, KEY_DATES):
        if path.exists():
            st = path.stat()
            if st.st_uid != 0 or st.st_mode & 0o077:
                path.chmod(0o600)
                try:
                    os.chown(path, 0, 0)
                except PermissionError:  # only when not run as root (tests)
                    pass
                done.append(f"fixed permissions on {path}")
    token = env.get("BUDDY_TOKEN", "")
    if len(token) < 32:
        find("act", "Buddy's access token is short", f"{len(token)} characters.",
             "Make a new one: sudo bash /opt/buddy/deploy/security.sh rotate-token")
    dates = {}
    if KEY_DATES.exists():
        dates = dict(l.split("=", 1) for l in KEY_DATES.read_text().splitlines() if "=" in l)
    secret_names = [n for n in env if re.search(r"KEY|TOKEN|SECRET|PASSWORD|_URL$", n) and env[n].strip()]
    old, unknown = [], []
    for name in secret_names:
        when = dates.get(name, "unknown")
        if when == "unknown":
            unknown.append(name)
        elif (datetime.date.today() - datetime.date.fromisoformat(when)).days > ROTATE_DAYS:
            old.append(name)
    if old:
        find("watch", f"{len(old)} keys are older than {ROTATE_DAYS} days", ", ".join(sorted(old)),
             "Make new ones at each provider, save each with sudo bash /opt/buddy/deploy/set.sh NAME, "
             "then delete the old ones at the provider.")
    if unknown:
        find("info", f"{len(unknown)} keys have no date yet", ", ".join(sorted(unknown)),
             "They were set before dates were kept. Rotate any that were ever pasted in a chat or email "
             "(set.sh NAME); the date starts then.")


def check_logs() -> None:
    sys.path.insert(0, str(APP / "backend"))
    try:
        from buddy.privacy import SECRET_PATTERNS
    except Exception:
        return
    keylike = [p for p in SECRET_PATTERNS if "ts\\.net" not in p.pattern]
    text = sh("journalctl", "-u", "buddy", "-u", "buddy-sandbox", "-u", "buddy-backup", "--since", "-7d",
              "-o", "cat", "--no-pager", timeout=300).stdout
    hits = sum(len(p.findall(text)) for p in keylike)
    if hits:
        find("act", f"{hits} possible keys found in the last week's logs",
             "Logs are root-only, but keys shouldn't be there at all.",
             "Rotate the keys Buddy uses (set.sh), then clear old logs: sudo journalctl --vacuum-time=1d. "
             "Tell me which part of Buddy logged it, so it can be fixed.")


def check_network() -> None:
    out = sh("ss", "-Htulnp").stdout
    exposed = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[4]
        host = local.rsplit(":", 1)[0].strip("[]")
        if host.startswith(("127.", "::1", "100.", "fd7a:115c:a1e0")) or local.endswith(":41641") \
                or host.startswith("%"):
            continue
        if any(f'"{name}"' in line for name in ("sshd", "tailscaled", "systemd-resolve", "chronyd")):
            continue  # expected; the firewall keeps SSH off the internet (checked below)
        if host in ("0.0.0.0", "*", "::") or not host.startswith(("10.", "172.", "192.168.")):  # nosec B104 (a check)
            exposed.append(f"{parts[0]} {local} {parts[-1][:40] if 'users:' in parts[-1] else ''}".strip())
    if exposed:
        find("watch", "Something listens on all network interfaces", "; ".join(exposed[:6]),
             "The firewall blocks it from the internet, but check it's expected; Buddy itself only "
             "listens on 127.0.0.1.")
    status = sh("ufw", "status", "verbose").stdout
    if "Status: active" not in status or "deny (incoming)" not in status:
        find("act", "The firewall isn't blocking incoming connections", status[:160],
             "sudo ufw default deny incoming && sudo ufw allow in on tailscale0 && sudo ufw --force enable")
    sshd = sh("sshd", "-T").stdout.lower()
    if "passwordauthentication yes" in sshd:
        find("watch", "SSH accepts passwords", "Only keys (or Tailscale SSH) should work.",
             "Set PasswordAuthentication no in /etc/ssh/sshd_config, then sudo systemctl restart ssh")
    if shutil.which("tailscale"):
        funnel = sh("tailscale", "funnel", "status").stdout
        if "Funnel on" in funnel or "(Funnel on)" in funnel:
            find("act", "Tailscale Funnel is on: Buddy may be reachable from the internet",
                 funnel[:200], "sudo tailscale funnel reset (Buddy only needs tailscale serve)")


def check_system() -> None:
    auto = Path("/etc/apt/apt.conf.d/20auto-upgrades")
    if not auto.exists() or '"1"' not in auto.read_text():
        find("act", "Automatic security updates are off", str(auto),
             "sudo dpkg-reconfigure -f noninteractive unattended-upgrades")
    if Path("/var/run/reboot-required").exists():
        find("watch", "A reboot is needed to finish security updates",
             "Ubuntu installed a kernel or system library update.",
             "It reboots automatically at 4:30am (set in security.sh install); or now: sudo reboot")
    pending = [l for l in sh("apt", "list", "--upgradable").stdout.splitlines()[1:] if "-security" in l]
    if pending:
        find("watch", f"{len(pending)} security updates are waiting", ", ".join(l.split("/")[0] for l in pending[:8]),
             "They usually install overnight; or now: sudo unattended-upgrade")
    backups = sorted(Path("/var/backups/buddy").glob("*.age"), key=lambda p: p.stat().st_mtime)
    if not backups or time.time() - backups[-1].stat().st_mtime > 2 * 86400:
        find("act", "No backup in the last 2 days", backups[-1].name if backups else "none found",
             "systemctl status buddy-backup.timer; run one now: sudo systemctl start buddy-backup")
    usage = shutil.disk_usage("/")
    if usage.used / usage.total > 0.85:
        find("watch", f"The disk is {int(usage.used / usage.total * 100)}% full", f"{usage.free // 2**30} GB free",
             "Old logs: sudo journalctl --vacuum-size=200M; Docker: sudo docker system prune")
    score = sh("systemd-analyze", "security", "buddy.service", "--no-pager").stdout
    m = re.search(r"Overall exposure level for buddy\.service:\s*([\d.]+)\s*(\w+)", score)
    if m and float(m.group(1)) > 5.0:
        find("watch", f"Buddy's service exposure is {m.group(1)} ({m.group(2)})", "Lower is better (under 5 is good).",
             "Apply the hardening: sudo bash /opt/buddy/deploy/security.sh harden")
    return m.group(1) if m else None


def main() -> None:
    started = datetime.datetime.now(datetime.timezone.utc)
    vulns: list[dict] = []
    steps = [] if NO_PATCH else [patch_os]
    for step in steps:
        try:
            step()
        except Exception as e:
            find("watch", f"{step.__name__} failed", type(e).__name__, "Run security.sh check and look at the output.")
    try:
        vulns = audit_python()
        if not NO_PATCH:
            patch_python(vulns)
            vulns = audit_python()
        if vulns:
            find("act" if any(not v["fix"] for v in vulns) else "watch",
                 f"{len(vulns)} known vulnerabilities in Buddy's packages",
                 ", ".join(f"{v['name']} {v['version']} ({v['id']})" for v in vulns[:6]),
                 "No fixed version yet for some: they're reviewed in docs/SECURITY.md; update.sh picks up fixes.")
    except Exception as e:
        find("watch", "The Python vulnerability scan failed", type(e).__name__, "Run security.sh check.")
    if not NO_PATCH:
        try:
            patch_sandbox()
        except Exception as e:
            find("watch", "The sandbox update failed", type(e).__name__, "Run sandbox.sh again.")
    exposure = None
    for label, check in (("keys", lambda: check_secrets(settings())), ("logs", check_logs),
                         ("network", check_network)):
        try:
            check()
        except Exception as e:
            find("watch", f"The {label} check failed to run", f"{type(e).__name__}: {str(e)[:120]}",
                 "Run sudo bash /opt/buddy/deploy/security.sh check and send me the output.")
    try:
        exposure = check_system()
    except Exception as e:
        find("watch", "A system check failed to run", type(e).__name__, "Run security.sh check.")

    order = {"act": 0, "watch": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 3))
    status = "act" if any(f["severity"] == "act" for f in findings) else (
        "watch" if any(f["severity"] == "watch" for f in findings) else "good")
    report = {"at": started.isoformat(), "status": status, "findings": findings, "done": done,
              "vulnerabilities": vulns, "exposure": exposure}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    try:
        shutil.chown(REPORT, "root", "buddy")
        REPORT.chmod(0o640)
    except (LookupError, PermissionError):
        pass
    with LOG.open("a") as f:
        f.write(f"{started:%Y-%m-%d %H:%M} {status}: {len(findings)} findings; done: {'; '.join(done) or 'nothing'}\n")
    if "--pretty" in sys.argv:
        print(f"\nStatus: {status}")
        for line in done:
            print(f"  done: {line}")
        for f in findings:
            print(f"\n  [{f['severity']}] {f['title']}\n    {f['detail']}\n    Fix: {f['fix']}")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
