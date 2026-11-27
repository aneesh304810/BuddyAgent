"""The threat watch: every day Buddy checks what is newly published about security against what this server
actually runs, and keeps a plain record of what is open, what was fixed, why each one matters and how it could
be used against this server.

  What is checked, daily
    Buddy's packages   every installed Python package and its exact version is looked up in OSV (the open
                       vulnerability database Google runs, which includes the GitHub and PyPI advisories)
    exploited now      CISA's Known Exploited Vulnerabilities list: a finding that is on it is being used in
                       real attacks, and is raised to critical; new entries about the software this server is
                       built on (Linux, OpenSSH, OpenSSL, Docker, Tailscale, Python, SQLite, sudo, glibc...)
                       are shown as "check" even when Buddy can't tell the exact version affected
    likelihood         EPSS (FIRST.org): the chance, from 0 to 100%, that a vulnerability is exploited in the
                       next 30 days
    the server         Ubuntu security updates waiting to be installed; the weekly root check's findings
                       (firewall, SSH, keys, backups, exposure: deploy/maintain.py); and Buddy's own settings
                       (the access token's strength, database encryption, developer mode, a stale check)

  What a finding says
    severity           critical | high | medium | low, from the published score, raised when it is exploited
    why it matters     what an attacker gains (read data, change data, take Buddy offline), in plain words
    how it could be used   from the published attack details (over the network or only on the server; with or
                       without signing in; with or without someone clicking) AND where that software sits in
                       Buddy: in the part reachable from the internet (the phone-call path), in the part that
                       reads outside files (email attachments, web pages, uploads), or inside only (reachable
                       only from the owner's private network with the access token)
    the fix            the version that fixes it, or the command to run

  The phone           the Buddy app tells the server the phone's own update levels (Android's security patch
                       date, the maker, the web view's version and when it was last updated; nothing personal).
                       CISA lists a flaw only once the vendor has a fix, so a phone whose security patch (or web
                       view update) is dated on or after the listing has it: that finding closes. Without a report
                       it stays open, as before.
  Open and fixed       a finding that a later scan no longer finds is marked fixed, with the date. Only a
                       source that answered can mark its findings fixed: an unreachable database changes
                       nothing. The owner can accept a risk with a reason; it stays listed as accepted.
  Patching             packages with a published fix are upgraded by the weekly root job (and rolled back if
                       Buddy doesn't come back healthy). "Patch now" asks the owner, then asks that same root
                       job to run at once; Buddy itself never runs as root.
  Alerts               only for a new critical or high finding, or one that is being exploited. The rest is in
                       the Security page.

Only package names and versions are sent out (to OSV); nothing personal. This reduces risk; it cannot prove the
server is safe: a flaw nobody has published yet is in no database.
"""

import json
import logging
import os
import re
import subprocess  # nosec B404: fixed commands only, see pending_updates
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sec_findings (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,          -- osv:GHSA-..:package | kev:CVE-.. | server:<title> | self:<check>
    source TEXT NOT NULL,              -- packages | exploited | server | settings
    severity TEXT NOT NULL,            -- critical | high | medium | low
    title TEXT NOT NULL,
    component TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    fixed_in TEXT NOT NULL DEFAULT '',
    ids TEXT NOT NULL DEFAULT '[]',    -- CVE and advisory ids
    score REAL,                        -- CVSS base score when published
    exploited INTEGER NOT NULL DEFAULT 0,
    epss REAL,                         -- chance of exploitation in 30 days, 0..1
    why TEXT NOT NULL DEFAULT '',
    exploit TEXT NOT NULL DEFAULT '',
    fix TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',   -- open | fixed | accepted
    note TEXT NOT NULL DEFAULT '',     -- how it was fixed, or why it was accepted
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    closed_on TEXT NOT NULL DEFAULT '',
    told INTEGER NOT NULL DEFAULT 0,
    layer TEXT NOT NULL DEFAULT 'app'
);
"""
# The layers of the setup, outside in. Every finding and every defence belongs to one.
LAYERS = (
    ("device", "Your phone and the Buddy app", "The Android phone, the Buddy app and the web view inside it."),
    ("network", "The network edge", "Tailscale (the private network Buddy is reached over), the firewall, and the "
                                    "one path open to the internet: phone calls."),
    ("server", "The server", "The Ubuntu machine: its updates, SSH, accounts and the lockdown around Buddy's service."),
    ("app", "Buddy itself", "The web server, the sign-in, and the packages that handle connections and Buddy's logic."),
    ("content", "Reading outside content", "The parts that open email attachments, web pages and uploaded files."),
    ("database", "The database", "One encrypted file (SQLCipher) holding everything Buddy remembers: email, money, "
                                 "health, documents and the vault. Market prices are in a separate plain file "
                                 "with nothing personal in it."),
    ("keys", "Keys, logs and backups", "The sealed key store, the logs (keys and tokens are masked) and the "
                                       "encrypted nightly backups."),
    ("sandbox", "The code sandbox", "The locked-down container where analysis code runs, with no network and no keys."),
    ("outside", "Outside services", "Claude, the banks' feed, email and the data providers Buddy calls."),
)
LAYER_OF_PLATFORM = {"android": "device", "chromium": "device", "samsung": "device", "tailscale": "network",
                     "docker": "sandbox", "runc": "sandbox", "containerd": "sandbox", "sqlite": "database",
                     "sqlcipher": "database", "openssl": "network"}
LAYER_WORDS = (("network", r"firewall|funnel|listens on all|tailnet|tailscale"),
               ("database", r"database"),
               ("keys", r"key|unencrypted|log lines|backup|secret"),
               ("sandbox", r"sandbox|docker"),
               ("outside", r"older than|rotate"),
               ("server", r"ssh|update|reboot|exposure|access|account|sudo|disk|check"))


def layer_of(f: dict) -> str:
    if f["source"] == "packages":
        name = f.get("component", "")
        return "database" if name in ("duckdb", "sqlcipher3", "sqlcipher3-binary") else \
            "keys" if name == "cryptography" else "content" if name in PARSERS else "app"
    text = f"{f.get('component', '')} {f.get('title', '')}".lower()
    if f["source"] == "exploited":
        return next((layer for name, layer in LAYER_OF_PLATFORM.items() if name in text), "server")
    if f["source"] == "outside":
        return "outside"
    if f["source"] == "settings":
        return "database" if "database" in text else "app"
    return next((layer for layer, words in LAYER_WORDS if re.search(words, text)), "server")
SCAN_KEY, DAY_KEY, DEVICE_KEY = "securitywatch:last", "securitywatch:day", "securitywatch:device"
DEVICE_DAYS = 30  # a phone report older than this isn't trusted to say the phone is up to date
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def set_device(memory: Memory, report: dict, now: datetime | None = None) -> dict:
    """What the Buddy app says about the phone's own updates: Android's security patch date, the maker, the web
    view's version and the day it was last updated. Nothing personal; anything else is dropped."""
    patch = str(report.get("security_patch") or "").strip()[:10]
    updated = str(report.get("webview_updated") or "").strip()[:10]
    out = {"security_patch": patch if _DAY.match(patch) else "",
           "android": re.sub(r"[^\w. -]", "", str(report.get("android") or ""))[:12],
           "maker": re.sub(r"[^\w. -]", "", str(report.get("maker") or ""))[:30].lower(),
           "webview": re.sub(r"[^\w. -]", "", str(report.get("webview") or ""))[:30],
           "webview_updated": updated if _DAY.match(updated) else "",
           "at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    memory.set(DEVICE_KEY, json.dumps(out))
    return out


def device(memory: Memory, today: date | None = None) -> dict:
    """The phone's last report, {} when there is none or it is older than DEVICE_DAYS."""
    try:
        got = json.loads(memory.get(DEVICE_KEY) or "{}")
        seen = datetime.fromisoformat(got["at"]).date()
    except (ValueError, KeyError, TypeError):
        return {}
    return got if ((today or local_now().date()) - seen).days <= DEVICE_DAYS else {}


def _phone_has_it(role: str, added: str, phone: dict) -> tuple[bool, str]:
    """(already fixed on the phone, what to do) for an exploited flaw in the phone's software."""
    if not phone:
        return False, ("Install the phone's system update (Settings > Software update) and update Chrome and Android "
                       "System WebView in the Play Store; then open the Buddy app, which tells Buddy the phone's "
                       "update level so this closes.")
    if role == "chromium":
        when = phone.get("webview_updated") or ""
        if when and when >= added:
            return True, ""
        return False, (f"Update Android System WebView and Chrome in the Play Store: the web view on your phone "
                       f"({phone.get('webview') or 'version unknown'}) was last updated "
                       f"{when or 'on an unknown day'}, before this was listed on {added}.")
    if role == "samsung" and phone.get("maker") and phone["maker"] != "samsung":
        return True, ""  # a Samsung flaw, and this phone isn't a Samsung
    patch = phone.get("security_patch") or ""
    if patch and patch >= added:
        return True, ""
    return False, (f"Install the phone's system update (Settings > Software update): its security patch is "
                   f"{patch or 'unknown'}, older than this listing ({added}).")
PATCH_REQUEST = Path(os.getenv("BUDDY_PATCH_REQUEST", "/var/lib/buddy/patch-request"))
ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
STALE_DAYS, KEV_DAYS, MAX_DETAILS = 8, 21, 80
# Where a package sits in Buddy decides how reachable a flaw in it is.
PUBLIC = {"fastapi", "starlette", "uvicorn", "h11", "httptools", "websockets", "wsproto", "anyio", "pydantic",
          "pydantic-core", "python-multipart", "pipecat-ai", "aiohttp"}
PARSERS = {"lxml", "pypdf", "pillow", "defusedxml", "python-docx", "openpyxl", "beautifulsoup4", "duckdb",
           "xlrd", "markdown", "html5lib", "pdfminer-six", "pymupdf", "trafilatura", "cryptography", "pyjwt",
           "numpy", "onnxruntime", "protobuf"}
# The software the server is built on: a newly exploited flaw in one of these is shown even without a version match.
PLATFORM = {"linux kernel": "the server's operating system", "ubuntu": "the server's operating system",
            "openssh": "remote sign-in to the server", "openssl": "encryption on every connection",
            "docker": "the code sandbox", "runc": "the code sandbox", "containerd": "the code sandbox",
            "tailscale": "the private network Buddy is reached over", "python": "the language Buddy runs on",
            "sqlite": "Buddy's database", "sqlcipher": "Buddy's encrypted database", "sudo": "administrator access",
            "glibc": "the system library every program uses", "systemd": "the service manager",
            "curl": "downloads during updates", "android": "the phone the Buddy app runs on",
            "chromium": "the web view inside the Buddy app", "samsung": "the phone the Buddy app runs on"}
EXPLAIN = (  # (words in a server finding's title, why it matters, how it could be used)
    ("firewall", "Without a firewall, every service on the server is reachable from the internet, not just Buddy.",
     "An attacker scanning the internet finds the open ports and tries each service for weak sign-ins or known flaws."),
    ("ssh accepts passwords", "Passwords can be guessed; keys can't.",
     "Bots try common passwords against SSH on every public server, thousands of times a day. One hit is full control."),
    ("funnel", "Funnel publishes part of the server to the whole internet instead of only your private network.",
     "Anyone with the address can reach what is published. Only the phone-call path should be, and it checks a "
     "signature on every request."),
    ("unencrypted", "Keys in plain text can be read by anything that gets root or a copy of the disk.",
     "A stolen backup or disk snapshot would hand over every service key at once."),
    ("access token", "The access token is the only thing between your private network and all of Buddy.",
     "A short token can be guessed by a device on your private network; with it, everything Buddy knows is readable."),
    ("keys are older", "A key that never changes stays useful to anyone who ever saw it.",
     "A key leaked months ago (in a log, a screenshot, an old device) still works until it is rotated."),
    ("log lines", "Keys written into logs are readable by anyone who can read the logs, and logs get copied around.",
     "Someone with read access to logs, or to a support bundle, takes the key and uses the service as you."),
    ("listens on all", "A service listening on every network interface may be reachable from outside.",
     "If the firewall ever opens or is misconfigured, that service is directly attackable."),
    ("security updates are waiting", "Each waiting update is a published flaw whose fix is not installed yet.",
     "Once a fix is public, attackers read it to build the attack; unpatched servers are found by scanning."),
    ("automatic security updates are off", "Fixes for published flaws would no longer install by themselves.",
     "The server falls further behind each day, and old flaws are the ones attackers use most."),
    ("reboot", "A kernel fix that is installed but not booted is not protecting the server yet.",
     "The old, vulnerable kernel is still running until the restart."),
    ("backup", "Without a recent backup, ransomware, a bad update or a disk failure loses your data.",
     "This isn't a way in; it decides how bad any other problem becomes."),
    ("service exposure", "The tighter Buddy's service is locked down, the less a flaw inside Buddy can reach.",
     "If Buddy were compromised through another flaw, a loosely confined service lets the attacker touch more of the server."),
    ("access", "People, devices and keys that shouldn't have access are a way in that needs no flaw at all.",
     "An old device or a forgotten SSH key on your private network can reach Buddy as if it were you."),
)


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA_SQL)
    memory._add_columns("sec_findings", {"layer": "TEXT NOT NULL DEFAULT 'app'"})
    memory.db.execute("UPDATE sec_findings SET layer = CASE WHEN title LIKE '%database%' THEN 'database' ELSE 'keys' "
                      "END WHERE layer = 'data'")  # the data layer became two


# --- what is installed --------------------------------------------------------------------------------------
def packages() -> dict[str, str]:
    """{normalised package name: version} for every Python package Buddy has installed."""
    from importlib import metadata  # noqa: PLC0415
    out = {}
    for d in metadata.distributions():
        name = (d.metadata.get("Name") or "").strip()
        if name and d.version:
            out[re.sub(r"[-_.]+", "-", name).lower()] = d.version
    return out


def pending_updates(run=None) -> list[str] | None:
    """Ubuntu packages with a security update waiting. None when it can't be told (not Ubuntu, apt missing)."""
    try:
        got = (run or subprocess.run)(["/usr/bin/apt", "list", "--upgradable"],  # nosec B603: fixed command, no input
                                      capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if got.returncode != 0:
        return None
    return sorted({line.split("/")[0] for line in got.stdout.splitlines() if "-security" in line and "/" in line})


def server_check() -> dict:
    """The weekly root check's report (deploy/maintain.py), {} when it hasn't run."""
    from .health import SECURITY_REPORT  # noqa: PLC0415
    try:
        return json.loads(Path(SECURITY_REPORT).read_text())
    except (OSError, ValueError):
        return {}


# --- the published scores, in plain words -------------------------------------------------------------------
def cvss(vector: str) -> dict:
    """A CVSS 3.x vector as {score, parts}. {} when it isn't one."""
    if not str(vector).startswith("CVSS:3"):
        return {}
    parts = dict(p.split(":", 1) for p in vector.split("/")[1:] if ":" in p)
    try:
        av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[parts["AV"]]
        ac = {"L": 0.77, "H": 0.44}[parts["AC"]]
        changed = parts["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if changed else 0.62, "H": 0.5 if changed else 0.27}[parts["PR"]]
        ui = {"N": 0.85, "R": 0.62}[parts["UI"]]
        c, i, a = ({"H": 0.56, "L": 0.22, "N": 0.0}[parts[k]] for k in ("C", "I", "A"))
    except KeyError:
        return {}
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
    exploitability = 8.22 * av * ac * pr * ui
    if impact <= 0:
        score = 0.0
    else:
        raw = min(1.08 * (impact + exploitability), 10) if changed else min(impact + exploitability, 10)
        score = int(raw * 10 + 0.99999) / 10  # CVSS rounds up to one decimal
    return {"score": score, "parts": parts}


def level(score: float | None, word: str = "") -> str:
    if score is not None:
        return "critical" if score >= 9 else "high" if score >= 7 else "medium" if score >= 4 else "low"
    return {"critical": "critical", "high": "high", "moderate": "medium", "medium": "medium",
            "low": "low"}.get(str(word).lower(), "medium")


def how(parts: dict) -> str:
    """How the flaw is attacked, from its published details."""
    if not parts:
        return "The advisory doesn't publish attack details."
    where = {"N": "over the network", "A": "from the same local network", "L": "only by something already running "
             "on the server", "P": "only with physical access to the machine"}.get(parts.get("AV"), "")
    who = {"N": "without signing in", "L": "with an ordinary signed-in account",
           "H": "only with administrator rights"}.get(parts.get("PR"), "")
    click = {"N": "and needs no action from you", "R": "and needs someone to open or click something"}.get(parts.get("UI"), "")
    hard = " It needs special conditions or timing to work." if parts.get("AC") == "H" else " It is simple to carry out."
    return f"It can be attacked {where}, {who}, {click}.{hard}"


def gains(parts: dict) -> str:
    if not parts:
        return ""
    out = [text for key, text in (("C", "read data it shouldn't"), ("I", "change data or run its own code"),
                                  ("A", "crash or stall Buddy")) if parts.get(key) in ("H", "L")]
    full = all(parts.get(k) == "H" for k in ("C", "I", "A"))
    return ("An attacker could take over the part of Buddy that uses it." if full else
            "An attacker could " + ", ".join(out) + "." if out else "")


def reach(package: str) -> str:
    """Where this package sits in Buddy, which decides who can get at a flaw in it."""
    if package in PUBLIC:
        return ("On this server: this package handles incoming connections. Buddy is reachable only from your "
                "private network (Tailscale) and needs the access token, except the phone-call path, which is "
                "open to the internet and checks a signature on each request. So this is among the most exposed "
                "parts.")
    if package in PARSERS:
        return ("On this server: this package reads content that comes from outside (email attachments, web "
                "pages, uploaded files). An attacker would not need to reach the server: sending you a crafted "
                "file or page that Buddy reads could be enough.")
    return ("On this server: this package is used inside Buddy only. To reach it an attacker would first need to "
            "be on your private network with the access token, or to have got in another way.")


# --- the public databases -----------------------------------------------------------------------------------
def osv_ids(memory: Memory, pkgs: dict, fetch=None) -> dict[str, list[str]]:
    """{package: [vulnerability ids]} for the installed versions."""
    names = sorted(pkgs)
    out: dict[str, list[str]] = {}
    for i in range(0, len(names), 200):
        chunk = names[i:i + 200]
        got = gateway.call(memory, "osv", "/v1/querybatch", fetch=fetch, purpose="security",
                           json_body={"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": pkgs[n]}
                                                  for n in chunk]})
        for name, result in zip(chunk, (got or {}).get("results") or []):
            ids = [v["id"] for v in (result or {}).get("vulns") or [] if v.get("id")]
            if ids:
                out[name] = ids
    return out


def osv_detail(memory: Memory, vuln_id: str, fetch=None) -> dict:
    return gateway.call(memory, "osv", f"/v1/vulns/{vuln_id}", fetch=fetch, purpose="security") or {}


def _fixed(detail: dict, package: str) -> str:
    versions = []
    for a in detail.get("affected") or []:
        if re.sub(r"[-_.]+", "-", str((a.get("package") or {}).get("name") or "")).lower() != package:
            continue
        for r in a.get("ranges") or []:
            versions += [e["fixed"] for e in r.get("events") or [] if e.get("fixed")]
    return sorted(versions, key=lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v)])[-1] if versions else ""


def kev(memory: Memory, fetch=None) -> list[dict]:
    got = gateway.call(memory, "cisa_kev", "/sites/default/files/feeds/known_exploited_vulnerabilities.json",
                       fetch=fetch, purpose="security") or {}
    return got.get("vulnerabilities") or []


def epss(memory: Memory, cves: list[str], fetch=None) -> dict[str, float]:
    out = {}
    for i in range(0, len(cves), 80):
        got = gateway.call(memory, "epss", "/data/v1/epss?cve=" + ",".join(cves[i:i + 80]), fetch=fetch,
                           purpose="security") or {}
        for row in got.get("data") or []:
            try:
                out[row["cve"]] = float(row["epss"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


# --- findings from each source ------------------------------------------------------------------------------
def from_packages(memory: Memory, pkgs: dict, fetch=None) -> list[dict]:
    found, details = [], {}
    by_package = osv_ids(memory, pkgs, fetch)
    for vuln_id in sorted({i for ids in by_package.values() for i in ids})[:MAX_DETAILS]:
        try:
            details[vuln_id] = osv_detail(memory, vuln_id, fetch)
        except Exception as e:
            log.info("securitywatch: detail %s: %s", vuln_id, type(e).__name__)
    for package, ids in by_package.items():
        for vuln_id in ids:
            d = details.get(vuln_id) or {}
            vector = next((s.get("score") for s in d.get("severity") or [] if str(s.get("type", "")).startswith("CVSS_V3")), "")
            scored = cvss(vector or "")
            word = (d.get("database_specific") or {}).get("severity") or ""
            fixed = _fixed(d, package)
            summary = " ".join(str(d.get("summary") or d.get("details") or "A published vulnerability.").split())[:300]
            found.append({
                "key": f"osv:{vuln_id}:{package}", "source": "packages",
                "severity": level(scored.get("score"), word), "score": scored.get("score"),
                "title": f"{package} {pkgs[package]}: {summary}", "component": package, "version": pkgs[package],
                "fixed_in": fixed, "ids": sorted({vuln_id, *[a for a in d.get("aliases") or [] if isinstance(a, str)]}),
                "why": (gains(scored.get("parts") or {}) or "A published flaw in a package Buddy uses.") + " " + summary,
                "exploit": how(scored.get("parts") or {}) + " " + reach(package),
                "fix": (f"Upgrade {package} to {fixed} or later. The weekly patch run does this by itself and "
                        "rolls back if Buddy doesn't come back healthy; \"patch now\" runs it at once."
                        if fixed else "No fixed version is published yet. Watch this one; if it is critical, "
                                      "ask me what can be switched off meanwhile."),
                "link": f"https://osv.dev/vulnerability/{vuln_id}"})
    return found


def from_exploited(entries: list[dict], findings: list[dict], today: date, phone: dict | None = None) -> list[dict]:
    """Mark findings that are on the exploited list, and add recent entries about the platform. An entry about the
    phone's software is left out when the phone's report shows an update dated on or after the listing."""
    phone_roles = ("chromium", "samsung", "android")
    by_cve = {e.get("cveID"): e for e in entries}
    for f in findings:
        hit = next((by_cve[i] for i in f["ids"] if i in by_cve), None)
        if hit:
            f["exploited"], f["severity"] = 1, "critical"
            f["why"] = ("This one is being used in real attacks right now (it is on CISA's Known Exploited "
                        "Vulnerabilities list" + (", and in ransomware" if str(hit.get("knownRansomwareCampaignUse"))
                                                  .lower() == "known" else "") + "). " + f["why"])
    out = []
    since = (today - timedelta(days=KEV_DAYS)).isoformat()
    for e in entries:
        if str(e.get("dateAdded") or "") < since:
            continue
        vendor, product = str(e.get("vendorProject") or "").lower().strip(), str(e.get("product") or "").lower()
        what = next((role for name, role in PLATFORM.items()
                     if vendor == name or re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", product)
                     or (name == "linux kernel" and vendor == "linux" and "kernel" in product)), "")
        cve = str(e.get("cveID") or "")
        if not what or any(cve in f["ids"] for f in findings):  # a package finding already carries it
            continue
        role = next((name for name in phone_roles if vendor == name or name in product
                     or (name == "chromium" and vendor == "google" and "chrom" in product)), "")
        phone_fix = ""
        if role:
            fixed, phone_fix = _phone_has_it(role, str(e.get("dateAdded") or ""), phone or {})
            if fixed:
                continue  # the phone's own update is dated after the listing: it has the vendor's fix
        out.append({
            "key": f"kev:{cve}", "source": "exploited", "severity": "high", "score": None, "exploited": 1,
            "title": f"Being exploited now: {e.get('vulnerabilityName') or cve}",
            "component": f"{e.get('vendorProject', '')} {e.get('product', '')}".strip(), "version": "", "fixed_in": "",
            "ids": [cve],
            "why": (f"This affects {what}. " + " ".join(str(e.get("shortDescription") or "").split())[:400]
                    + " It was added to the list of flaws used in real attacks on " + str(e.get("dateAdded")) + "."),
            "exploit": ("Attackers are already using it. I can't tell from here whether the exact version on your "
                        "server is the affected one, so treat it as open until the patch run has installed the "
                        "vendor's fix."),
            "fix": phone_fix or (" ".join(str(e.get("requiredAction") or "Apply the vendor's update.").split())[:300]
                                 + " On this server: \"patch now\" installs Ubuntu's security updates."),
            "link": f"https://nvd.nist.gov/vuln/detail/{cve}"})
    return out


# --- outside services: what Buddy is connected to, what they did lately, and whether one was breached ---------
BREACH_DAYS, IDLE_DAYS = 365, 60


def _domain(host: str) -> str:
    name = re.sub(r"^https?://", "", str(host)).split("/")[0].split(":")[0].lower()
    parts = name.split(".")
    return ".".join(parts[-3:] if len(parts) > 2 and parts[-2] in ("co", "com", "org", "gov") and len(parts[-1]) == 2
                    else parts[-2:])


def services(memory: Memory, env=None, today: date | None = None) -> list[dict]:
    """Every outside service Buddy is set up to talk to: what it is for, whether it may see personal data,
    its recent activity, and whether the owner disconnected it."""
    today = today or local_now().date()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent = {}
    for table in ("api_calls", "model_calls"):
        try:
            for r in memory.db.execute(f"SELECT provider, COUNT(*) AS n, SUM(1 - ok) AS bad, MAX(at) AS last FROM "  # nosec B608
                                       f"{table} WHERE at >= ? GROUP BY provider", (week,)):
                recent[r["provider"]] = (r["n"], r["bad"] or 0)
        except Exception:
            log.debug("securitywatch: no %s table yet", table)
    out = []
    for row in gateway.status(memory, env):
        if not row["set_up"] and not gateway.is_off(memory, row["name"]):
            continue
        entry = gateway.CATALOG[row["name"]]
        aliases = gateway.MODEL_NAMES.get(row["name"], (row["name"],))
        n = sum(recent.get(a, (0, 0))[0] for a in aliases)
        bad = sum(recent.get(a, (0, 0))[1] for a in aliases)
        last = row["last_at"][:10]
        idle = (today - date.fromisoformat(last)).days if last else None
        out.append({"name": row["name"], "title": row["title"], "group": row["group"], "used_for": row["used_for"],
                    "sees": "your personal data" if row["privacy"] == "personal" else "public data only",
                    "personal": row["privacy"] == "personal",
                    "reaches": sorted({_domain(h) for h in entry.get("hosts") or []}),
                    "calls_7_days": n, "failed_7_days": bad, "last_used": last, "idle_days": idle,
                    "last_error": row["last_error"][:120] if row["last_ok"] is False else "",
                    "off": gateway.is_off(memory, row["name"]),
                    "can_disconnect": row["name"] not in gateway.ALWAYS_ON,
                    "why_not": gateway.ALWAYS_ON.get(row["name"], ""), "incidents": []})
    return out


def breaches(memory: Memory, fetch=None) -> list[dict]:
    """The public list of companies that reported a data breach (Have I Been Pwned). Nothing is sent."""
    got = gateway.call(memory, "hibp", "/api/v3/breaches", fetch=fetch, purpose="security",
                       headers={"User-Agent": "buddy-security-watch"})
    return got if isinstance(got, list) else []


def from_services(rows: list[dict], breached: list[dict] | None, exploited: list[dict], today: date) -> list[dict]:
    """Findings about the services Buddy is connected to: a published breach, a flaw in their product being
    exploited, a key that suddenly stopped being accepted, and keys for services no longer used."""
    out = []
    since = (today - timedelta(days=BREACH_DAYS)).isoformat()
    for s in rows:
        domains, word = set(s["reaches"]), s["name"].split("_")[0].lower()
        for b in breached or []:
            added = str(b.get("AddedDate") or b.get("BreachDate") or "")[:10]
            if added < since or str(b.get("Domain") or "").lower() not in domains:
                continue
            fresh = added >= (today - timedelta(days=90)).isoformat()
            what = ", ".join((b.get("DataClasses") or [])[:5]) or "not stated"
            s["incidents"].append(f"Reported a data breach, published {added}")
            out.append({
                "key": f"vendor:{s['name']}:{b.get('Name')}", "source": "outside", "component": s["title"],
                "severity": "high" if s["personal"] and fresh else "medium",
                "title": f"{s['title']} reported a data breach (published {added})",
                "why": (f"Buddy is connected to {s['title']} ({s['used_for'] or s['group']}), which can see "
                        f"{s['sees']}. The breach, dated {b.get('BreachDate', 'unknown')}, exposed: {what}. "
                        "A published breach is about their customers' accounts; it doesn't by itself mean your "
                        "key or your data was taken, but the key Buddy uses there should be treated as exposed."),
                "exploit": ("With a stolen key someone could use the service as you: run up charges, and for a "
                            "service that sees personal data, read what Buddy sends or stores there."),
                "fix": (f"Rotate its key: sudo bash /opt/buddy/deploy/keys.sh rotate. To cut it off meanwhile, "
                        f"disconnect {s['title']} on the Security page (Outside services)."),
                "link": f"https://haveibeenpwned.com/PwnedWebsites#{b.get('Name', '')}"})
        for e in exploited:
            if str(e.get("dateAdded") or "") < (today - timedelta(days=90)).isoformat():
                continue
            if str(e.get("vendorProject") or "").lower().strip() == word and len(word) > 3:
                cve = str(e.get("cveID") or "")
                s["incidents"].append(f"A flaw in their product is being exploited ({cve})")
                out.append({
                    "key": f"vendor:{s['name']}:{cve}", "source": "outside", "severity": "medium", "exploited": 1,
                    "component": s["title"], "ids": [cve],
                    "title": f"A flaw in {s['title']}'s product is being exploited: {e.get('vulnerabilityName') or cve}",
                    "why": (f"Buddy is connected to {s['title']}, which can see {s['sees']}. "
                            + " ".join(str(e.get("shortDescription") or "").split())[:300]),
                    "exploit": "This is in their product, not on your server. It matters if they haven't patched "
                               "their own service; watch their status page.",
                    "fix": f"If they confirm customer impact, rotate the key and disconnect {s['title']} until it is fixed.",
                    "link": f"https://nvd.nist.gov/vuln/detail/{cve}"})
        if re.search(r"\b(401|403|unauthori[sz]ed|forbidden|invalid.{0,12}key)\b", s["last_error"], re.I) and not s["off"]:
            s["incidents"].append("Its key was refused on the last call")
            out.append({
                "key": f"vendor:{s['name']}:refused", "source": "outside", "severity": "medium", "component": s["title"],
                "title": f"{s['title']} refused Buddy's key on its last call",
                "why": "A key that suddenly stops working was revoked, expired, or changed by someone.",
                "exploit": "If you didn't change it, check the account at the provider for sign-ins you don't recognise.",
                "fix": "Sign in at the provider, check recent activity, make a new key and save it with keys.sh rotate."})
        if s["idle_days"] is not None and s["idle_days"] >= IDLE_DAYS and not s["off"] and s["can_disconnect"]:
            out.append({
                "key": f"vendor:{s['name']}:idle", "source": "outside", "severity": "low", "component": s["title"],
                "title": f"{s['title']} has a key but hasn't been used for {s['idle_days']} days",
                "why": "Every stored key is one more thing that can leak. One you don't use is all risk and no benefit.",
                "exploit": "A leaked key for a forgotten service can be abused for a long time before anyone notices.",
                "fix": f"Disconnect {s['title']} on the Security page, and remove its key if you won't use it."})
    return out


def from_server(report: dict, waiting: list[str] | None, today: date) -> list[dict]:
    out = []
    if not report:
        out.append({"key": "server:no-check", "source": "server", "severity": "medium",
                    "title": "The weekly server check has never run",
                    "why": "The firewall, SSH, keys, backups and patches are not being checked.",
                    "exploit": "This isn't a way in by itself; it means a real gap would go unnoticed.",
                    "fix": "Run: sudo bash /opt/buddy/deploy/security.sh install"})
    else:
        try:
            age = (today - datetime.fromisoformat(report["at"]).date()).days
        except (KeyError, ValueError):
            age = 999
        if age > STALE_DAYS:
            out.append({"key": "server:stale-check", "source": "server", "severity": "medium",
                        "title": f"The weekly server check last ran {age} days ago",
                        "why": "Patches and the server checks are not running on schedule.",
                        "exploit": "Published fixes are not being installed, so known flaws stay open longer.",
                        "fix": "Run: sudo bash /opt/buddy/deploy/security.sh check, then security.sh install"})
        for f in report.get("findings") or []:
            title = str(f.get("title") or "")
            if "security updates are waiting" in title.lower() and waiting is not None:
                continue  # counted live below
            why, used = next(((w, h) for words, w, h in EXPLAIN if words in title.lower()),
                             ("Found by the weekly server check.", "See the detail and the fix."))
            out.append({"key": "server:" + re.sub(r"\d+", "N", title.lower())[:80], "source": "server",
                        "severity": {"act": "high", "watch": "medium"}.get(f.get("severity"), "low"),
                        "title": title, "why": f"{why} {str(f.get('detail') or '')[:200]}".strip(), "exploit": used,
                        "fix": str(f.get("fix") or "")[:300]})
    if waiting:
        why, used = next((w, h) for words, w, h in EXPLAIN if words == "security updates are waiting")
        out.append({"key": "server:updates-waiting", "source": "server", "severity": "medium",
                    "title": f"{len(waiting)} Ubuntu security update{'s are' if len(waiting) != 1 else ' is'} waiting",
                    "component": ", ".join(waiting[:10]), "why": why, "exploit": used,
                    "fix": "They install by themselves each night; \"patch now\" installs them at once."})
    return out


def _config() -> tuple[str, bool]:
    """(the access token, developer mode) as Buddy is running now."""
    from .config import settings  # noqa: PLC0415
    return settings.access_token or "", bool(settings.dev_mode)


def from_settings(env=None) -> list[dict]:
    """What Buddy can see about its own configuration."""
    env = os.environ if env is None else env
    out = []
    token, dev_mode = _config()
    if dev_mode:
        out.append({"key": "self:dev-mode", "source": "settings", "severity": "critical",
                    "title": "Developer mode is on", "why": "In developer mode Buddy can answer without the access token.",
                    "exploit": "Any device that can reach the server could use Buddy as you, with no sign-in.",
                    "fix": "Remove BUDDY_DEV_MODE from the settings and restart Buddy."})
    if not token:
        out.append({"key": "self:no-token", "source": "settings", "severity": "critical",
                    "title": "No access token is set",
                    "why": "The access token is the sign-in for everything Buddy knows.",
                    "exploit": "Without one Buddy refuses all requests, or in developer mode accepts all of them.",
                    "fix": "sudo bash /opt/buddy/deploy/set.sh BUDDY_ACCESS_TOKEN"})
    elif len(token) < 32:
        why, used = next((w, h) for words, w, h in EXPLAIN if words == "access token")
        out.append({"key": "self:short-token", "source": "settings", "severity": "high",
                    "title": f"The access token is short ({len(token)} characters)", "why": why, "exploit": used,
                    "fix": "Set a new one of 43 characters or more: sudo bash /opt/buddy/deploy/keys.sh rotate BUDDY_ACCESS_TOKEN"})
    if not (env.get("BUDDY_DB_KEY") or "").strip():
        out.append({"key": "self:db-plain", "source": "settings", "severity": "high",
                    "title": "The database is not encrypted",
                    "why": "Everything Buddy remembers (email, money, health, documents) sits in one file.",
                    "exploit": "A copy of the disk, a backup or a snapshot would be readable by whoever has it.",
                    "fix": "Set BUDDY_DB_KEY (sudo bash /opt/buddy/deploy/set.sh BUDDY_DB_KEY) and follow docs/SECURITY.md to encrypt the existing file."})
    return out


# --- the scan -----------------------------------------------------------------------------------------------
def _save(memory: Memory, found: list[dict], ran: set[str], today: str) -> dict:
    """Upsert what was found; what a source that ran no longer finds is fixed. Returns {"new": [...], "fixed": [...]}."""
    new, seen = [], set()
    for f in found:
        seen.add(f["key"])
        row = memory.db.execute("SELECT id, status, told FROM sec_findings WHERE key = ?", (f["key"],)).fetchone()
        values = (f["source"], f["severity"], f["title"][:400], f.get("component", "")[:200], f.get("version", "")[:40],
                  f.get("fixed_in", "")[:40], json.dumps(f.get("ids") or []), f.get("score"), int(f.get("exploited") or 0),
                  f.get("epss"), f.get("why", "")[:900], f.get("exploit", "")[:900], f.get("fix", "")[:500],
                  f.get("link", "")[:200], layer_of(f))
        if row is None:
            memory.db.execute(
                "INSERT INTO sec_findings (source, severity, title, component, version, fixed_in, ids, score, "
                "exploited, epss, why, exploit, fix, link, layer, key, first_seen, last_seen) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, f["key"], today, today))
            new.append(f)
        else:
            reopened = row["status"] == "fixed"
            memory.db.execute(
                "UPDATE sec_findings SET source = ?, severity = ?, title = ?, component = ?, version = ?, fixed_in = ?, "
                "ids = ?, score = ?, exploited = ?, epss = ?, why = ?, exploit = ?, fix = ?, link = ?, layer = ?, last_seen = ?"
                + (", status = 'open', closed_on = '', note = 'found again after it was fixed', told = 0" if reopened else "")
                + " WHERE id = ?", (*values, today, row["id"]))
            if reopened:
                new.append(f)
    fixed = []
    for row in memory.db.execute("SELECT id, key, source, title FROM sec_findings WHERE status IN ('open', 'accepted')").fetchall():
        if row["source"] in ran and row["key"] not in seen:
            memory.db.execute("UPDATE sec_findings SET status = 'fixed', closed_on = ?, note = ? WHERE id = ?",
                              (today, "no longer found by the scan" + (": the package was upgraded" if
                                                                       row["source"] == "packages" else ""), row["id"]))
            fixed.append(row["title"])
    memory.db.commit()
    return {"new": new, "fixed": fixed}


def scan(memory: Memory, fetch=None, pkgs: dict | None = None, report: dict | None = None,
         waiting: list[str] | None | bool = False, env=None, today: date | None = None) -> dict:
    """Run every check now. Returns {"new", "fixed", "sources": {name: "ok" | why it failed}}."""
    _ready(memory)
    today = today or local_now().date()
    found, sources, ran = [], {}, set()
    try:
        found += from_packages(memory, packages() if pkgs is None else pkgs, fetch)
        sources["Buddy's packages (OSV)"], _ = "ok", ran.add("packages")
    except Exception as e:
        log.warning("securitywatch: OSV failed: %s", type(e).__name__)
        sources["Buddy's packages (OSV)"] = f"couldn't be reached ({type(e).__name__}); earlier findings kept as they were"
    exploited_list: list[dict] = []
    try:
        exploited_list = kev(memory, fetch)
        found += from_exploited(exploited_list, found, today, device(memory, today))
        sources["Exploited now (CISA KEV)"], _ = "ok", ran.add("exploited")
    except Exception as e:
        log.warning("securitywatch: KEV failed: %s", type(e).__name__)
        sources["Exploited now (CISA KEV)"] = f"couldn't be reached ({type(e).__name__})"
    try:  # the services Buddy is connected to: breaches, exploited flaws, refused or forgotten keys
        connected = services(memory, env, today)
        breached = None
        try:
            breached = breaches(memory, fetch)
            sources["Breaches at connected services (Have I Been Pwned)"] = "ok"
        except Exception as e:
            sources["Breaches at connected services (Have I Been Pwned)"] = f"couldn't be reached ({type(e).__name__})"
        found += from_services(connected, breached, exploited_list, today)
        if breached is not None:
            ran.add("outside")
    except Exception:
        log.warning("securitywatch: the services check failed", exc_info=True)
    cves = sorted({i for f in found for i in f.get("ids") or [] if i.startswith("CVE-")})
    if cves:
        try:
            odds = epss(memory, cves, fetch)
            for f in found:
                got = [odds[i] for i in f.get("ids") or [] if i in odds]
                if got:
                    f["epss"] = max(got)
            sources["Likelihood (EPSS)"] = "ok"
        except Exception as e:
            sources["Likelihood (EPSS)"] = f"couldn't be reached ({type(e).__name__})"
    found += from_server(server_check() if report is None else report,
                         pending_updates() if waiting is False else waiting, today)
    found += from_settings(env)
    ran |= {"server", "settings"}
    sources["The server and Buddy's settings"] = "ok"
    out = _save(memory, found, ran, today.isoformat())
    memory.set(SCAN_KEY, json.dumps({"at": datetime.now(timezone.utc).isoformat(), "sources": sources,
                                     "packages": len(packages() if pkgs is None else pkgs)}))
    return {**out, "sources": sources}


def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    return now.hour >= 6 and memory.get(DAY_KEY) != now.date().isoformat()


def alerts(memory: Memory) -> list[str]:
    """New critical, high or exploited findings to tell the owner about, each once. spoken || shown."""
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute(
        "SELECT * FROM sec_findings WHERE status = 'open' AND told = 0 AND (severity IN ('critical', 'high') OR "
        "exploited = 1) ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, id")]
    memory.db.execute("UPDATE sec_findings SET told = 1 WHERE status = 'open' AND told = 0")
    memory.db.commit()
    if not rows:
        return []
    lines = ["**🛡 Security: " + (f"{len(rows)} new findings" if len(rows) > 1 else "a new finding") + "**", ""]
    for r in rows[:6]:
        lines += [f"**{r['severity'].title()}{' · being exploited now' if r['exploited'] else ''}: {r['title'][:160]}**",
                  f"- Why it matters: {r['why'][:300]}", f"- How it could be used: {r['exploit'][:300]}",
                  f"- Fix: {r['fix'][:240]}", ""]
    lines.append("The Security page has all of it; say \"patch now\" to install the fixes at once.")
    worst = rows[0]
    spoken = (f"Security: {len(rows)} new finding{'s' if len(rows) != 1 else ''}. The most serious is "
              f"{worst['severity']}{', and being exploited now' if worst['exploited'] else ''}. Details are on screen.")
    return [f"{spoken} || " + "\n".join(lines)]


# --- the report ---------------------------------------------------------------------------------------------
def _view(r: dict) -> dict:
    out = {k: r[k] for k in ("id", "layer", "severity", "title", "component", "version", "fixed_in", "why", "exploit", "fix",
                             "link", "status", "note", "first_seen", "last_seen", "closed_on", "score") if r[k] not in ("", None)}
    out["source"] = {"packages": "Buddy's packages", "exploited": "Exploited now", "server": "The server",
                     "settings": "Buddy's settings", "outside": "Outside services"}.get(r["source"], r["source"])
    out["ids"] = json.loads(r["ids"] or "[]")
    out["exploited"] = bool(r["exploited"])
    if r["epss"] is not None:
        out["chance_of_exploit_30_days"] = f"{r['epss']:.1%}"
    return out


def defences(report: dict, env=None, phone: dict | None = None) -> list[dict]:
    """What is already in place, each with where the evidence comes from."""
    env = os.environ if env is None else env
    phone = phone or {}
    token, _ = _config()
    facts = report.get("facts") or {}
    out = [
        {"name": "Reachable only over your private network", "layer": "network", "ok": True,
         "detail": "Buddy listens for Tailscale devices only; the phone-call path is the one part open to the "
                   "internet and checks a signature on every request."},
        {"name": "Access token on every request", "layer": "app", "ok": len(token) >= 32,
         "detail": f"{len(token)} characters, compared in constant time."},
        {"name": "Database encrypted", "layer": "database", "ok": bool((env.get("BUDDY_DB_KEY") or "").strip()),
         "detail": "SQLCipher, with the key outside the database file."},
        {"name": "Lockout after failed sign-ins", "layer": "app", "ok": True,
         "detail": "Ten wrong tokens from one address in 15 minutes block it for 15 minutes; each is logged."},
        {"name": "Browser protections", "layer": "device", "ok": True,
         "detail": "No framing, no content sniffing, no referrer, HTTPS only; the API description is not published."},
        {"name": "Code runs in a sandbox", "layer": "sandbox", "ok": True,
         "detail": "Anything Buddy runs for analysis runs in a locked-down container with no network and no keys."},
        {"name": "Known-vulnerable packages blocked before release", "layer": "app", "ok": True,
         "detail": "Every code change is scanned on GitHub: tests, dependency vulnerabilities, a code scan and leaked secrets."},
    ]
    out += [{"name": "Private documents sit in a vault", "layer": "database", "ok": True,
             "detail": "Vault documents show only a summary; the full text opens after your yes."},
            {"name": "Personal data is kept out of the plain price file", "layer": "database", "ok": True,
             "detail": "The price book holds market and deal prices only; your journal, portfolio and offers "
                       "are in the encrypted database."},
            {"name": "Keys and tokens masked in logs and prompts", "layer": "keys", "ok": True,
             "detail": "A filter removes anything shaped like a key before a log line is written."}]
    out += [{"name": "Outside services reached only through Buddy Connect", "layer": "outside", "ok": True,
             "detail": "Every outside call goes through one gateway: rate-limited, logged, and services marked "
                       "public can never be sent personal data."},
            {"name": "Bank access is read-only", "layer": "outside", "ok": True,
             "detail": "Buddy can read balances and transactions; it has no way to move money."}]
    patch = phone.get("security_patch") or ""
    try:
        recent = (local_now().date() - date.fromisoformat(patch)).days <= 62  # this month's or last month's patch
    except ValueError:
        recent = False
    out.append({"name": "Phone's security updates", "layer": "device", "ok": recent,
                "detail": (f"Security patch {patch or 'not reported'}"
                           + (f"; web view {phone['webview']}" if phone.get("webview") else "")
                           + (f" (updated {phone['webview_updated']})" if phone.get("webview_updated") else "")
                           + f"; reported by the Buddy app on {str(phone.get('at', ''))[:10]}."
                           if phone else "The Buddy app reports the phone's security patch when it opens; no report "
                                         "yet (update the app and open it).")})
    for key, name, layer in (("firewall", "Firewall blocking incoming connections", "network"),
                             ("ssh_keys_only", "SSH keys only, no passwords", "server"),
                             ("secrets_sealed", "Keys encrypted at rest", "keys"),
                             ("auto_updates", "Automatic security updates", "server"),
                             ("backup_recent", "Recent encrypted backup", "keys")):
        if key in facts:
            out.append({"name": name, "layer": layer, "ok": bool(facts[key]), "detail": "From the weekly server check."})
    return out


def layers(open_: list[dict], fixed: list[dict], accepted: list[dict], guards: list[dict]) -> list[dict]:
    """Each layer of the setup with its level (the worst open finding, else good), counts and defences."""
    out = []
    for key, name, what in LAYERS:
        mine = [f for f in open_ if f["layer"] == key]
        worst = next((s for s in ORDER if any(f["severity"] == s for f in mine)), "")
        held = [g for g in guards if g["layer"] == key]
        out.append({"key": key, "name": name, "what": what, "level": worst or "good",
                    "open": len(mine), "exploited": sum(1 for f in mine if f["exploited"]),
                    "fixed": sum(1 for f in fixed if f["layer"] == key),
                    "accepted": sum(1 for f in accepted if f["layer"] == key),
                    "defences": len(held), "defences_on": sum(1 for g in held if g["ok"])})
    return out


def trend(rows: list[dict], today: date, weeks: int = 8) -> list[dict]:
    """Per week, how many findings were found and how many were fixed, and how many were open at its end."""
    out = []
    monday = today - timedelta(days=today.weekday())
    for n in range(weeks - 1, -1, -1):
        start, end = monday - timedelta(weeks=n), monday - timedelta(weeks=n) + timedelta(days=6)
        a, b = start.isoformat(), end.isoformat()
        out.append({"week": a,
                    "found": sum(1 for r in rows if a <= r["first_seen"] <= b),
                    "fixed": sum(1 for r in rows if r["status"] == "fixed" and a <= r["closed_on"] <= b),
                    "open_at_end": sum(1 for r in rows if r["first_seen"] <= b and
                                       (r["status"] == "open" or (r["closed_on"] or "9999") > b))})
    return out


def report(memory: Memory, env=None) -> dict:
    _ready(memory)
    rows = [dict(r) for r in memory.db.execute("SELECT * FROM sec_findings ORDER BY CASE severity WHEN 'critical' "
                                               "THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
                                               "exploited DESC, last_seen DESC")]
    since = (local_now().date() - timedelta(days=90)).isoformat()
    open_ = [_view(r) for r in rows if r["status"] == "open"]
    fixed = sorted((_view(r) for r in rows if r["status"] == "fixed" and r["closed_on"] >= since),
                   key=lambda r: r["closed_on"], reverse=True)
    accepted = [_view(r) for r in rows if r["status"] == "accepted"]
    last = json.loads(memory.get(SCAN_KEY) or "{}")
    counts = {s: sum(1 for r in open_ if r["severity"] == s) for s in ORDER}
    worst = next((s for s in ORDER if counts[s]), "")
    check = server_check()
    guards = defences(check, env, device(memory))
    connected = services(memory, env)
    for s in connected:  # what the last scan said about each service
        s["incidents"] = [f["title"] for f in open_ if f.get("component") == s["title"] and f["source"] == "Outside services"]
    all_fixed = [_view(r) for r in rows if r["status"] == "fixed"]
    return {
        "services": connected,
        "services_summary": {"connected": sum(1 for s in connected if not s["off"]),
                             "see_personal_data": sum(1 for s in connected if s["personal"] and not s["off"]),
                             "disconnected": sum(1 for s in connected if s["off"]),
                             "with_incidents": sum(1 for s in connected if s["incidents"])},
        "layers": layers(open_, all_fixed, accepted, guards), "trend": trend(rows, local_now().date()),
        "status": {"critical": "Act now", "high": "Needs attention", "medium": "A few things to tidy",
                   "low": "Good, with small notes", "": "Good: nothing open"}[worst] if last else "Not scanned yet",
        "worst": worst, "counts": counts, "exploited_open": sum(1 for r in open_ if r["exploited"]),
        "open": open_, "fixed": fixed[:60], "accepted": accepted,
        "last_scan": last.get("at", ""), "sources": last.get("sources") or {}, "packages_checked": last.get("packages", 0),
        "server_check": {"at": check.get("at", ""), "done": (check.get("done") or [])[:12]},
        "defences": guards, "patch_requested": PATCH_REQUEST.exists(),
        "limits": "This checks what is published against what this server runs, every day. It lowers the risk; it "
                  "can't prove the server is safe: a flaw nobody has published yet is in no database, and I can't "
                  "see the exact versions of everything in the operating system from inside Buddy.",
    }


def accept(memory: Memory, finding_id, reason: str) -> str:
    _ready(memory)
    reason = " ".join(str(reason or "").split())[:300]
    if len(reason) < 8:
        raise ToolError("Say why this risk is acceptable; it is kept with the finding.")
    row = memory.db.execute("SELECT title FROM sec_findings WHERE id = ? AND status = 'open'", (finding_id,)).fetchone()
    if row is None:
        raise ToolError("No open finding with that id.")
    memory.db.execute("UPDATE sec_findings SET status = 'accepted', note = ?, closed_on = ? WHERE id = ?",
                      (f"accepted by the owner: {reason}", local_now().date().isoformat(), finding_id))
    memory.db.commit()
    return f"Marked as accepted: {row['title'][:120]}. It stays listed, and reopens if I can't find it and then find it again."


def request_patch() -> str:
    """Ask the root patch job to run now (a systemd path unit watches for this file). No parameters are passed."""
    try:
        PATCH_REQUEST.parent.mkdir(parents=True, exist_ok=True)
        PATCH_REQUEST.write_text(datetime.now(timezone.utc).isoformat())
    except OSError as e:
        return (f"I couldn't ask the server to patch ({type(e).__name__}). Run it yourself: "
                "sudo bash /opt/buddy/deploy/security.sh check")
    return ("Patching has started: Ubuntu's security updates, Buddy's vulnerable packages (rolled back if Buddy "
            "doesn't come back healthy) and the sandbox image. It takes a few minutes and Buddy may restart; I'll "
            "scan again afterwards and tell you what was fixed.")


TOOL = {
    "name": "security_watch",
    "description":
        "Buddy's own security: a daily check of newly published vulnerabilities and flaws being exploited now "
        "against what this server runs, plus the server and settings checks. report: what is open (severity, "
        "why it matters, how it could be used against this server, the fix), what was fixed and when, what the "
        "owner accepted, and the defences in place. Use it for 'is my server safe', 'any vulnerabilities', "
        "'security report', 'what did you fix'. scan: run every check now. explain (id): one finding in full. "
        "accept (id, reason): the owner accepts a risk. patch_now: install the fixes at once (asks the owner; "
        "Buddy may restart). Explain findings in plain words; never say the server is safe or secure, only "
        "what was checked and what is open. services: every outside service Buddy is connected to (what it is "
        "for, whether it sees personal data, calls and failures in the last 7 days, last used, any published "
        "breach or exploited flaw at that provider). disconnect (service, reason): stop sending anything to a "
        "service at once (its key stays stored; say to rotate it after a breach); reconnect (service) undoes it. "
        "The SOC 2 readiness document is the security_report tool.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["report", "scan", "explain", "accept", "patch_now", "services",
                                              "disconnect", "reconnect"]},
        "id": {"type": "integer"}, "reason": {"type": "string"},
        "service": {"type": "string", "description": "disconnect, reconnect: the service's name from services"}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict, thrift=None) -> str:
    import asyncio  # noqa: PLC0415
    action = args.get("action")
    if action == "scan":
        got = await asyncio.to_thread(scan, memory)
        full = report(memory)
        return json.dumps({"new": [f["title"] for f in got["new"]][:20], "fixed_since_last_scan": got["fixed"][:20],
                           "sources": got["sources"], "status": full["status"], "counts": full["counts"],
                           "open": full["open"][:15]}, ensure_ascii=False)
    if action == "explain":
        _ready(memory)
        row = memory.db.execute("SELECT * FROM sec_findings WHERE id = ?", (args.get("id"),)).fetchone()
        if row is None:
            raise ToolError("No finding with that id.")
        return json.dumps(_view(dict(row)), ensure_ascii=False)
    if action == "accept":
        return accept(memory, args.get("id"), args.get("reason") or "")
    if action == "patch_now":
        if thrift is None:
            return "I need your yes before patching, and I can't ask right now."

        async def go() -> str:
            return request_patch()
        return thrift.ask_action("Install the security fixes now? Ubuntu's updates, Buddy's vulnerable packages and "
                                 "the sandbox image; Buddy may restart for a minute, and a broken package update is "
                                 "rolled back by itself.", go)
    if action == "services":
        return json.dumps({"services": services(memory), "note": "incidents are filled by the daily scan; see "
                           "report for the findings"}, ensure_ascii=False)
    if action in ("disconnect", "reconnect"):
        wanted = str(args.get("service") or "").strip().lower()
        match = next((n for n, e in gateway.CATALOG.items() if wanted and (wanted == n or wanted in e["title"].lower())), "")
        if not match:
            raise ToolError("Which service? Use its name from the services list.")
        return gateway.set_off(memory, match, action == "disconnect", str(args.get("reason") or ""))
    full = report(memory)
    full["open"], full["fixed"] = full["open"][:25], full["fixed"][:25]
    return json.dumps(full, ensure_ascii=False)
