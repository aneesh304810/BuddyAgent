"""SOC 2 readiness assessment: Buddy's controls mapped to the AICPA Trust Services Criteria (2017,
revised points of focus 2022), with evidence read live from the server, a gap and remediation plan,
and a Word document to download.

This is a self-assessment. It is NOT a SOC 2 report: only an independent licensed CPA firm can
examine controls and issue a SOC 2 Type I or Type II report. The document says so on its first page.

Evidence:
  - the weekly root security check (deploy/maintain.py -> /var/lib/buddy/security.json): its findings
    and "facts" (firewall, SSH, keys sealed and their ages, backups, timers, access review, patches)
  - Buddy itself: the access token, database encryption, the access log and lockouts (guard.py),
    retention periods, read-only bank access, the code sandbox, connector isolation
  - the policies in docs/ (SECURITY.md, ACCESS_CONTROL.md)
What only the owner can show (vendor reports, training, a restore test, the annual sign-off) is
marked "Owner to confirm" with what to keep on file.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import guard, makers
from .config import settings
from .memory import Memory
from .tools import ToolError

MET, PARTIAL, GAP, CONFIRM = "Met", "Partly met", "Gap", "Owner to confirm"
SCORE = {MET: 1.0, PARTIAL: 0.5, GAP: 0.0}
DOCS = Path(__file__).resolve().parents[2] / "docs"
REPORT_LINK_DAYS = 365
STALE_CHECK_DAYS = 8  # the security check runs weekly
NOTICE = ("This is a SOC 2 readiness self-assessment prepared automatically by Buddy from its own configuration "
          "and logs. It is not a SOC 2 report and is not an attestation: only an independent licensed CPA firm can "
          "examine controls under AICPA attestation standards (AT-C 105 and 205) and issue a SOC 2 Type I or Type II "
          "report. Use it to prepare for an examination and to answer partner security questionnaires; do not "
          "describe it as audited, certified or SOC 2 compliant.")
SCOPE = ("Trust Services Categories in scope: Security (Common Criteria CC1-CC9), Availability (A1), "
         "Confidentiality (C1) and Privacy (P1-P8). Processing Integrity is out of scope: Buddy does not "
         "process transactions for others.")
SUBSERVICE = [
    ("DigitalOcean", "Server hosting and physical security (carved out)", "SOC 2 Type II report, yearly"),
    ("Tailscale", "Private network, device identity, TLS certificates", "SOC 2 Type II report, yearly"),
    ("Anthropic", "AI model (Claude) that processes conversations", "SOC 2 Type II report, yearly"),
    ("Plaid", "Read-only bank connections", "SOC 2 Type II report, yearly"),
    ("Cloudflare", "Company website and email routing (no customer data)", "SOC 2 Type II report, yearly"),
    ("GitHub", "Source code, CI and change history", "SOC 2 Type II report, yearly"),
]
DATA = ("Conversations and memories, calendar and email the owner connects, bank transactions and balances "
        "(read-only, from Plaid), card benefits, documents the owner uploads, an optional voiceprint (biometric), "
        "and the access log (time, network address, path and result of each request).")


# --- evidence ---------------------------------------------------------------------------------
def security_check() -> dict:
    from .health import SECURITY_REPORT

    try:
        return json.loads(Path(SECURITY_REPORT).read_text())
    except (OSError, ValueError):
        return {}


def evidence(memory: Memory) -> dict:
    from .finance import REPORT_KEEP_DAYS
    from . import agents, sandbox, transcripts, tutor, usage, diagnostics

    sec = security_check()
    at = sec.get("at", "")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(at)).days if at else None
    except ValueError:
        age = None
    key = os.getenv("BUDDY_DB_KEY", "")
    token = settings.access_token or ""
    return {
        "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "check_at": at, "check_age_days": age, "check_status": sec.get("status", ""),
        "findings": sec.get("findings", []), "facts": sec.get("facts", {}),
        "vulnerabilities": sec.get("vulnerabilities", []),
        "token_set": bool(token), "token_length": len(token), "dev_mode": bool(settings.dev_mode),
        "db_encrypted": len(key) == 64,
        "access": guard.guard.stats(30),
        "failures": guard.guard.recent(15, only_failures=True),
        "lockout": {"failures": guard.LOCKOUT_FAILURES, "window_minutes": guard.LOCKOUT_WINDOW // 60,
                    "minutes": guard.LOCKOUT_SECONDS // 60},
        "retention": {**{f"report {k}": v for k, v in REPORT_KEEP_DAYS.items()},
                      "access log": guard.ACCESS_LOG_DAYS, "room transcripts": transcripts.KEEP_DAYS,
                      "English practice recordings": tutor.KEEP_DAYS, "AI usage records": usage.KEEP_CALLS_DAYS,
                      "app diagnostics": diagnostics.KEEP_DAYS},
        "core_only": list(agents.CORE_ONLY),
        "sandbox": bool(sandbox.available()),
        "docs": sorted(p.name for p in DOCS.glob("*.md")) if DOCS.exists() else [],
        "bank_read_only": True,  # Plaid products requested: transactions (+ liabilities); no payment products
        "voiceprint": bool(memory.get("voiceprint:on")),
    }


# --- the controls -----------------------------------------------------------------------------
def _fact(ev: dict, name: str, default=None):
    return ev["facts"].get(name, default)


def _no_check(ev: dict) -> str:
    return "No weekly security check on record yet (run: sudo bash /opt/buddy/deploy/security.sh check)." \
        if not ev["check_at"] else ""


def _bool(ev: dict, name: str, good: bool, yes: str, no: str) -> tuple[str, str]:
    value = _fact(ev, name)
    if value is None:
        return GAP, _no_check(ev) or f"Not measured ({name})."
    return (MET, yes) if bool(value) == good else (GAP, no)


def controls(ev: dict) -> list[dict]:
    f = ev["facts"]
    act = [x for x in ev["findings"] if x.get("severity") == "act"]
    timers = f.get("timers", {})
    acc = ev["access"]
    out: list[dict] = []

    def add(cid: str, criteria: str, control: str, status: str, evidence: str, fix: str = "", priority: str = ""):
        out.append({"id": cid, "criteria": criteria, "control": control, "status": status, "evidence": evidence,
                    "fix": fix, "priority": priority or ("High" if status == GAP else "Medium" if status == PARTIAL
                                                         else "Low" if status == CONFIRM else "")})

    has = lambda name: name in ev["docs"]  # noqa: E731
    # CC1 control environment
    add("CC1.1", "CC1.1, CC1.4", "A written information security program (FTC Safeguards Rule) and code of conduct, "
        "reviewed and approved by the owner every year.", PARTIAL if has("SECURITY.md") else GAP,
        "docs/SECURITY.md and the Information Security Program document exist." if has("SECURITY.md") else
        "No security program found in docs/.",
        "Sign and date the Information Security Program once a year; keep the signed copy.")
    add("CC1.3", "CC1.2, CC1.3", "A named security owner (Qualified Individual) with authority over the system.",
        CONFIRM, "Buddy Labs is run by its owner, who is the security owner.",
        "Name the security owner in the Information Security Program; add an independent advisor when staff join.")
    add("CC1.4", "CC1.4, CC1.5", "Security awareness training for everyone with access, at hiring and every year.",
        CONFIRM, "One person has access today.", "Complete a short security awareness course each year and keep the "
        "certificate; add background checks and an NDA for anyone given access.")
    # CC2 communication
    fresh = ev["check_age_days"] is not None and ev["check_age_days"] <= STALE_CHECK_DAYS
    add("CC2.2", "CC2.1, CC2.2", "Security findings reach the owner automatically (weekly check, daily health digest).",
        MET if fresh else GAP, f"Last security check {ev['check_at'][:10] or 'never'}; status {ev['check_status'] or '-'}.",
        "" if fresh else "Turn the weekly check back on: sudo bash /opt/buddy/deploy/security.sh install")
    add("CC2.3", "CC2.3, P1.1", "A public privacy policy, a security contact (security@buddylabs.io, "
        "/.well-known/security.txt) and a privacy contact (privacy@buddylabs.io).", CONFIRM,
        "Published on buddylabs.io; Buddy can't check the website from the server.",
        "Check once a quarter that the policy and both mailboxes work (send a test email).")
    # CC3 risk assessment
    add("CC3.1", "CC3.1, CC3.2, CC3.3", "A risk assessment: threats, likelihood, impact and the control for each, "
        "updated every year and after big changes.", PARTIAL,
        "The risk register in this report is generated from live findings; the weekly check scans for new risks.",
        "Review the risk register in this report once a year, add business risks (fraud, vendor failure), sign it.")
    # CC4 monitoring
    add("CC4.1", "CC4.1", "Controls are evaluated continuously: weekly automated security check and patching.",
        MET if fresh else GAP, f"Check age: {ev['check_age_days'] if ev['check_age_days'] is not None else 'n/a'} days; "
        f"timer: {timers.get('buddy-maintain.timer', 'unknown')}.",
        "" if fresh else "sudo bash /opt/buddy/deploy/security.sh install")
    add("CC4.2", "CC4.2", "Deficiencies found are fixed promptly, most serious first.",
        MET if fresh and not act else PARTIAL if fresh else GAP,
        f"{len(act)} findings need action now" + (": " + "; ".join(x["title"] for x in act[:4]) if act else "."),
        "Fix the 'act' findings listed in the security check." if act else "")
    # CC5 control activities
    add("CC5.2", "CC5.1, CC5.2, CC5.3", "Written policies for access control, encryption, logging, change management, "
        "incident response and data retention.", MET if has("ACCESS_CONTROL.md") and has("SECURITY.md") else PARTIAL,
        "Policies in docs/: " + ", ".join(x for x in ev["docs"] if x in ("SECURITY.md", "ACCESS_CONTROL.md", "KEYS.md",
                                                                          "SANDBOX.md", "MONEY.md")) + ".",
        "" if has("ACCESS_CONTROL.md") else "Add docs/ACCESS_CONTROL.md.")
    # CC6 logical and physical access
    token_ok = ev["token_set"] and ev["token_length"] >= 32 and not ev["dev_mode"]
    add("CC6.1a", "CC6.1", "Every API call and app connection needs the access token (32+ random characters, checked "
        "in constant time); there is no anonymous access and no public API documentation.",
        MET if token_ok else GAP, f"Token set: {ev['token_set']}; length {ev['token_length']}; developer mode "
        f"{'on' if ev['dev_mode'] else 'off'}.", "" if token_ok else "sudo bash /opt/buddy/deploy/security.sh rotate-token")
    add("CC6.1b", "CC6.1, C1.1", "Data at rest is encrypted: Buddy's database with SQLCipher (AES-256), keys sealed to "
        "the server with systemd-creds, backups encrypted with age.",
        MET if ev["db_encrypted"] and f.get("keys_sealed") and f.get("backups_encrypted") else
        PARTIAL if ev["db_encrypted"] else GAP,
        f"Database encrypted: {ev['db_encrypted']}; keys sealed: {f.get('keys_sealed', 'not measured')}; "
        f"backups encrypted: {f.get('backups_encrypted', 'not measured')}.",
        "Seal the keys: sudo bash /opt/buddy/deploy/security.sh seal" if not f.get("keys_sealed") else
        "" if ev["db_encrypted"] else "Set BUDDY_DB_KEY with set.sh and migrate the database.")
    add("CC6.1c", "CC6.1", "Documents Buddy makes (reports, charts) and uploaded files are protected on disk.", PARTIAL,
        "Files are in Buddy's private folder (owner-only permissions); the provider encrypts the server's disks.",
        "Keep a copy of DigitalOcean's statement on disk encryption; consider encrypting made files with the "
        "database key.", "Low")
    old_keys = f.get("keys_older_than_rotation")
    add("CC6.1d", "CC6.1", f"Keys are rotated at least every {f.get('rotate_days', 90)} days and kept out of chats, "
        "code and logs.", (MET if old_keys == 0 and f.get("log_lines_with_keys_7d", 0) == 0 else PARTIAL)
        if old_keys is not None else GAP,
        (f"{f.get('keys', 0)} keys; {old_keys} older than the rotation period; {f.get('keys_without_date', 0)} without "
         f"a date; key-like lines in last week's logs: {f.get('log_lines_with_keys_7d', 0)}.") if old_keys is not None
        else _no_check(ev), "Rotate the old keys at each provider and save them with set.sh NAME." if old_keys else "")
    add("CC6.2", "CC6.2", "Access is granted only from an approved list (people, SSH keys, tailnet devices), "
        "reviewed every quarter.", (MET if f.get("access_review_last") and not f.get("access_review_due") else
                                    PARTIAL if f.get("access_review_last") else GAP),
        f"Last access review: {f.get('access_review_last') or 'none'}" +
        (f" by {f['access_review_by']}" if f.get("access_review_by") else "") + ".",
        "sudo bash /opt/buddy/deploy/access.sh review" if not f.get("access_review_last") or
        f.get("access_review_due") else "")
    enforce_on = timers.get("buddy-access.timer") == "active"
    add("CC6.3", "CC6.2, CC6.3", "Access not on the approved list is removed automatically every day; offboarding "
        "removes a person's accounts, keys and devices in one step; roles (owner, admin, server) are enforced by the "
        "tailnet policy.", MET if enforce_on and f.get("access_differences") == 0 else PARTIAL if enforce_on else GAP,
        f"Daily enforcement timer: {timers.get('buddy-access.timer', 'not measured')}; differences from the approved "
        f"list: {f.get('access_differences', 'not measured')}.",
        "" if enforce_on else "sudo bash /opt/buddy/deploy/access.sh install")
    add("CC6.4", "CC6.4", "Physical security of the data center.", CONFIRM,
        "Carved out to DigitalOcean (subservice organization).", "Download DigitalOcean's SOC 2 Type II report each year "
        "and keep it with this assessment.")
    add("CC6.5", "CC6.5, C1.2, P4.3", "Data is deleted when no longer needed, on fixed retention periods.", MET,
        "Retention: " + ", ".join(f"{k} {v} days" for k, v in ev["retention"].items()) + ".")
    net_ok = (f.get("firewall_deny_incoming") and not f.get("tailscale_funnel_on") and f.get("exposed_listeners") == 0)
    add("CC6.6a", "CC6.6", "Buddy is not on the internet: it listens only on 127.0.0.1 and is reached through the "
        "private tailnet; the firewall denies incoming connections; no Funnel.",
        MET if net_ok else GAP if f.get("firewall_deny_incoming") is False or f.get("tailscale_funnel_on") else
        PARTIAL if f else GAP,
        f"Firewall denies incoming: {f.get('firewall_deny_incoming', 'not measured')}; Funnel on: "
        f"{f.get('tailscale_funnel_on', 'not measured')}; other services listening on all interfaces: "
        f"{f.get('exposed_listeners', 'not measured')}.", "" if net_ok else "See the network findings in the security check.")
    add("CC6.6b", "CC6.6, CC6.8", f"Brute-force protection: {ev['lockout']['failures']} failed sign-ins from one address "
        f"within {ev['lockout']['window_minutes']} minutes lock it out for {ev['lockout']['minutes']} minutes; security "
        "headers (Content-Security-Policy, HSTS, no framing, no sniffing) on every page.", MET,
        f"Failed sign-ins in the last 30 days: {acc['failed_sign_ins']}; lockouts: {acc['lockouts']}.")
    ssh_ok = f.get("ssh_password_login") is False
    add("CC6.6c", "CC6.1, CC6.6", "Administrator access to the server: SSH keys or Tailscale SSH with re-check every "
        "12 hours, never passwords.", MET if ssh_ok else GAP,
        f"SSH password login: {f.get('ssh_password_login', 'not measured')}.",
        "" if ssh_ok else "Set PasswordAuthentication no in /etc/ssh/sshd_config and restart ssh.")
    add("CC6.7", "CC6.7, C1.1", "Data in transit is encrypted: HTTPS with Tailscale certificates over WireGuard, HSTS; "
        "Plaid, Anthropic and other providers over TLS.", MET, "The app accepts only https:// server links; Buddy "
        "listens on 127.0.0.1 and is published only through tailscale serve.")
    add("CC6.8", "CC6.8", "Untrusted code runs only in a locked-down sandbox; connectors and add-on agents never get "
        "core keys (bank, database, access token, AI key).", MET if ev["sandbox"] else PARTIAL,
        f"Code sandbox available: {ev['sandbox']}; keys withheld from add-ons: {', '.join(ev['core_only'])}.",
        "" if ev["sandbox"] else "Install the sandbox: sudo bash /opt/buddy/deploy/sandbox.sh", "Low")
    # CC7 system operations
    vulns = f.get("vulnerabilities", len(ev["vulnerabilities"]))
    updates = f.get("auto_security_updates")
    add("CC7.1", "CC7.1", "Vulnerability management: automatic OS security updates, weekly dependency scan and "
        "patching (pip-audit), code scanning in CI (bandit, gitleaks).",
        MET if updates and vulns == 0 and not f.get("security_updates_waiting") else PARTIAL if updates else GAP,
        f"Automatic security updates: {updates if updates is not None else 'not measured'}; known vulnerable "
        f"packages: {vulns}; OS security updates waiting: {f.get('security_updates_waiting', 'n/a')}; service "
        f"exposure score: {f.get('service_exposure', 'n/a')} (under 5 is good).",
        "sudo dpkg-reconfigure -f noninteractive unattended-upgrades" if not updates else
        "Packages with no fix yet are reviewed in docs/SECURITY.md." if vulns else "")
    logging_on = acc["total"] > 0
    add("CC7.2", "CC7.2", f"Security events are logged and monitored: an access log of every API call and connection "
        f"(kept {guard.ACCESS_LOG_DAYS} days), failed sign-ins and lockouts, a weekly scan of logs for leaked keys, a "
        "daily health check.", MET if logging_on else PARTIAL,
        f"Access log: {acc['total']} entries since {acc['oldest'][:10] or '-'}; {acc['entries']} in the last 30 days "
        f"from {acc['addresses']} addresses.", "" if logging_on else "The access log starts with the next request.")
    add("CC7.3", "CC7.3, CC7.4", "Security incidents are evaluated and handled with a written incident response plan "
        "(contain, assess, notify within legal deadlines, recover, learn).", PARTIAL,
        "The Information Security Program includes the incident response plan; lockouts and findings are recorded.",
        "Run a 30-minute tabletop exercise once a year (e.g. 'a laptop with SSH access is stolen') and keep notes.")
    backup_ok = f.get("backup_age_hours") is not None and f["backup_age_hours"] <= 48
    add("CC7.5", "CC7.5, A1.2", "Encrypted daily backups kept off the main database, with a restore procedure.",
        MET if backup_ok and timers.get("buddy-backup.timer") == "active" else PARTIAL if backup_ok else GAP,
        f"Backups: {f.get('backups', 'not measured')}; newest {f.get('backup_age_hours', 'n/a')} hours old; timer "
        f"{timers.get('buddy-backup.timer', 'not measured')}.",
        "" if backup_ok else "sudo systemctl start buddy-backup; check systemctl status buddy-backup.timer")
    # CC8 change management
    add("CC8.1", "CC8.1", "Changes are made in code under version control (GitHub), tested in CI (tests, dependency "
        "audit, code and secret scans) and installed by the owner; Buddy never deploys itself, and its own code "
        "changes are proposals the owner reviews.", MET,
        "CI workflows: security checks and app build; updates install only with deploy/update.sh run by the owner.",
        "Turn on branch protection for the main branch (require CI to pass).", "Low")
    # CC9 risk mitigation
    add("CC9.1", "CC9.1", "Business continuity: the server can be rebuilt from code and an encrypted backup.",
        PARTIAL if backup_ok else GAP, "deploy/bootstrap.sh rebuilds the server; deploy/restore.sh restores a backup.",
        "Restore the latest backup onto a test server once a year and record how long it took.")
    add("CC9.2", "CC9.2", "Vendors and subservice organizations are assessed every year.", CONFIRM,
        "Vendors: " + ", ".join(v[0] for v in SUBSERVICE) + ".",
        "Download each vendor's SOC 2 report (or security page) yearly and note any issues.")
    # A1 availability
    disk = f.get("disk_used_pct")
    add("A1.1", "A1.1", "Capacity is monitored (disk space, service health) with alerts.",
        MET if disk is not None and disk < 85 else PARTIAL if disk is not None else GAP,
        f"Disk used: {disk if disk is not None else 'not measured'}%.", "" if disk is None or disk < 85
        else "Free space: sudo journalctl --vacuum-size=200M")
    add("A1.3", "A1.3", "Recovery is tested.", CONFIRM, "deploy/restore.sh exists.",
        "Do a restore test once a year (see CC9.1) and keep the notes.")
    # C1 confidentiality
    add("C1.1", "C1.1", "Confidential information is identified and kept out of logs: keys and tokens are masked in "
        "every log line; the access log never stores message content, bodies or query strings.",
        MET if not f.get("log_lines_with_keys_7d") else PARTIAL,
        f"Key-like lines in last week's logs: {f.get('log_lines_with_keys_7d', 'not measured')}.",
        "See the leaked-key finding in the security check." if f.get("log_lines_with_keys_7d") else "")
    # Privacy
    add("P2.1", "P2.1, P3.1", "Only what is needed is collected; bank access is read-only (no payments or transfers), "
        "and Buddy asks before any action on the owner's behalf.", MET,
        "Plaid products: transactions and liabilities only; actions go through confirm_action.")
    add("P4.1", "P4.1, P6.1", "Personal data is used only to serve the owner and shared only with the providers "
        "needed (Anthropic for AI, Plaid for banks); never sold.", MET, "Data flows are described in the privacy policy.")
    add("P5.1", "P5.1, P5.2", "The owner can see, export and delete their data (memories, documents, voiceprint).",
        MET, "Chat commands forget facts, documents and the voiceprint; the dashboard and reports show money data.")
    add("P6.x", "P2.1, P4.2 (Illinois BIPA)", "Biometric data (voiceprint): written consent before enrolment, a public "
        "retention schedule, deletion when turned off and within 3 years of last use.",
        PARTIAL if ev["voiceprint"] else CONFIRM,
        f"Voiceprint {'on' if ev['voiceprint'] else 'off'}; the Biometric Information Privacy Policy is written.",
        "Add the consent screen before voice enrolment and automatic deletion (open item).", "Medium")
    add("P8.1", "P8.1", "Privacy questions and complaints have a contact and a response time.", CONFIRM,
        "privacy@buddylabs.io, auto-reply promises a reply within 3 business days.",
        "Keep a simple log of privacy requests and replies.")
    return out


def score(items: list[dict]) -> int:
    rated = [SCORE[c["status"]] for c in items if c["status"] in SCORE]
    return round(100 * sum(rated) / len(rated)) if rated else 0


def risks(ev: dict, items: list[dict]) -> list[list[str]]:
    """A starting risk register: the usual risks for a system like Buddy, rated from the evidence."""
    status = {c["id"]: c["status"] for c in items}
    level = lambda *ids: ("High" if any(status.get(i) == GAP for i in ids) else  # noqa: E731
                          "Medium" if any(status.get(i) in (PARTIAL, CONFIRM) for i in ids) else "Low")
    return [
        ["Stolen access token or phone", "Lockout, token rotation, tailnet device approval", level("CC6.1a", "CC6.2")],
        ["Server compromised through an unpatched flaw", "Auto updates, weekly patching, not on the internet",
         level("CC7.1", "CC6.6a")],
        ["Keys leaked (chat, logs, code)", "Hidden prompts, sealed keys, log masking, gitleaks", level("CC6.1d", "C1.1")],
        ["Data lost (disk failure, mistake)", "Encrypted daily backups, restore script", level("CC7.5", "CC9.1")],
        ["Unauthorized admin access", "SSH keys only, quarterly review, daily enforcement", level("CC6.6c", "CC6.3")],
        ["A vendor is breached", "Least data shared, vendor reviews", level("CC9.2")],
        ["Biometric law (BIPA) violation", "Consent and retention schedule", level("P6.x")],
    ]


# --- the report -------------------------------------------------------------------------------
def _counts(items: list[dict]) -> dict:
    return {s: sum(1 for c in items if c["status"] == s) for s in (MET, PARTIAL, GAP, CONFIRM)}


def _plan(items: list[dict]) -> list[dict]:
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted((c for c in items if c["status"] != MET and c["fix"]), key=lambda c: (order.get(c["priority"], 3), c["id"]))


def spec(ev: dict, items: list[dict]) -> dict:
    c = _counts(items)
    plan = _plan(items)
    acc = ev["access"]
    f = ev["facts"]
    return {
        "title": "SOC 2 Readiness Assessment: Buddy",
        "subtitle": f"Buddy Labs · prepared {ev['now'][:10]} · self-assessment, not an audit",
        "sections": [
            {"heading": "Important notice", "paragraphs": [NOTICE]},
            {"heading": "Summary", "paragraphs": [
                f"Readiness: {score(items)}% of the rated controls are in place ({c[MET]} met, {c[PARTIAL]} partly met, "
                f"{c[GAP]} gaps). {c[CONFIRM]} controls need the owner to keep evidence on file.",
                f"Server evidence from the weekly security check of {ev['check_at'][:10] or 'never (not run yet)'}."],
             "bullets": [f"{p['priority']}: {p['control'][:110]} -> {p['fix']}" for p in plan[:5]]},
            {"heading": "System description", "paragraphs": [
                "Buddy is a personal AI assistant. A FastAPI service runs on one DigitalOcean server and is reached "
                "only over the owner's private Tailscale network by the Buddy Android app and web page. It uses "
                "Anthropic's Claude for language, Plaid for read-only bank data, and the owner's connected accounts.",
                SCOPE, "Data: " + DATA,
                "People: the owner is the only person with access and is the security owner.",
                "Subservice organizations (carved-out method): their controls are not covered here."],
             "table": {"columns": ["Provider", "Role", "Evidence to keep"], "rows": [list(v) for v in SUBSERVICE]}},
            {"heading": "Controls and evidence",
             "table": {"columns": ["ID", "Criteria", "Control", "Status", "Evidence"],
                       "rows": [[x["id"], x["criteria"], x["control"], x["status"], x["evidence"]] for x in items]}},
            {"heading": "Gaps and remediation plan",
             "paragraphs": [] if plan else ["No open items."],
             "table": {"columns": ["Priority", "ID", "Action", "Status now"],
                       "rows": [[p["priority"], p["id"], p["fix"], p["status"]] for p in plan]} if plan else None},
            {"heading": "Risk register", "table": {"columns": ["Risk", "Main controls", "Current risk"],
                                                   "rows": risks(ev, items)}},
            {"heading": "Evidence appendix",
             "bullets": [f"{k.replace('_', ' ')}: {v}" for k, v in sorted(f.items()) if not isinstance(v, (dict, list))]
             + [f"timer {k}: {v}" for k, v in sorted(f.get("timers", {}).items())]
             + [f"access log, last 30 days: {acc['entries']} requests, {acc['failed_sign_ins']} failed sign-ins, "
                f"{acc['lockouts']} lockouts; kept {acc['keep_days']} days",
                "open security findings: " + (", ".join(f"[{x.get('severity')}] {x.get('title')}" for x in
                                                         ev["findings"][:10]) or "none")]},
            {"heading": "Recent failed sign-ins",
             "paragraphs": [] if ev["failures"] else ["None recorded."],
             "table": {"columns": ["When (UTC)", "Address", "Path", "Result"],
                       "rows": [[r["at"].replace("T", " ")[:16], r["ip"] or "-", r["path"] or "-",
                                 "locked out" if r["kind"] == "lockout" else str(r["status"])]
                                for r in ev["failures"]]} if ev["failures"] else None},
            {"heading": "Getting an actual SOC 2 report", "bullets": [
                "Fix the High items above, then keep the evidence for each control (this report, screenshots, the "
                "access reviews in /var/lib/buddy/access-reviews, vendor reports).",
                "Choose an independent CPA firm licensed to perform SOC 2 examinations.",
                "Start with a SOC 2 Type I (controls designed well, at one date), then a Type II (controls worked "
                "over 3 to 12 months).",
                "Generate this assessment monthly during the observation period: it shows the controls kept working.",
                "Until a CPA firm issues a report, say 'we follow SOC 2 practices and are preparing for an "
                "examination', never 'SOC 2 compliant' or 'SOC 2 certified'."]},
        ],
    }


def markdown(ev: dict, items: list[dict], url: str) -> str:
    c = _counts(items)
    lines = [f"[Download the full report (Word)]({url})", "",
             f"**Readiness {score(items)}%**: {c[MET]} met, {c[PARTIAL]} partly met, {c[GAP]} gaps, "
             f"{c[CONFIRM]} for you to confirm.", "", f"> {NOTICE}", "", "## Gaps and remediation plan", ""]
    plan = _plan(items)
    lines += [f"- **{p['priority']} · {p['id']}**: {p['fix']}" for p in plan] or ["- No open items."]
    lines += ["", "## Controls", "", "| ID | Control | Status |", "|---|---|---|"]
    lines += [f"| {x['id']} | {x['control'][:90]} | {x['status']} |" for x in items]
    acc = ev["access"]
    lines += ["", "## Access, last 30 days", "",
              f"- {acc['entries']} requests from {acc['addresses']} addresses; {acc['failed_sign_ins']} failed sign-ins; "
              f"{acc['lockouts']} lockouts.",
              f"- Security check: {ev['check_at'][:10] or 'not run yet'} ({ev['check_status'] or '-'}).", "",
              f"The download link works for {REPORT_LINK_DAYS} days; ask for a new report any time."]
    return "\n".join(lines)


def summary(items: list[dict]) -> str:
    c = _counts(items)
    plan = _plan(items)
    top = plan[0]["fix"] if plan else ""
    return (f"SOC 2 readiness {score(items)}%: {c[MET]} controls met, {c[PARTIAL]} partly, {c[GAP]} gaps, "
            f"{c[CONFIRM]} for you to confirm." + (f" First: {top}" if top else ""))


def generate(memory: Memory) -> dict:
    """Assess, build the Word document, save it and the report; returns the report's id and link."""
    from .finance import purge_reports
    from .jobs import SCHEMA as JOBS_SCHEMA

    ev = evidence(memory)
    items = controls(ev)
    try:
        data = makers.build_docx(memory, spec(ev, items))
    except ImportError as e:
        raise ToolError("Word documents need python-docx on the server (update.sh installs it).") from e
    file = makers.save(memory, data, f"Buddy SOC 2 readiness {ev['now'][:10]}", "docx", "report")
    url = makers.link(file["id"], days=REPORT_LINK_DAYS)
    base = (memory.get("server:base_url") or "").rstrip("/")
    full = base + url if base.startswith("https://") else url
    now = datetime.now(timezone.utc).isoformat()
    memory.db.executescript(JOBS_SCHEMA)
    purge_reports(memory)
    text = summary(items)
    job_id = memory.db.execute(
        "INSERT INTO jobs (kind, title, params, status, created_at, started_at, finished_at, summary, report, findings, "
        "sources, delivered) VALUES ('soc_report', ?, '{}', 'done', ?, ?, ?, ?, ?, ?, '[]', 1)",
        (f"SOC 2 readiness, {ev['now'][:10]}", now, now, now, text, markdown(ev, items, full),
         json.dumps([p["fix"] for p in _plan(items)]))).lastrowid
    memory.db.commit()
    guard.guard.record("report", "", "", "", "/compliance/soc-report", 200)
    try:  # the newest one is also in the library, so Buddy can answer questions about it
        from . import companydocs
        companydocs.publish(memory, "live/soc-readiness", "SOC 2 readiness (latest).md",
                            f"# SOC 2 readiness assessment, {ev['now'][:10]}\n\n" + markdown(ev, items, full)
                            + "\n\n## Evidence\n\n" + "\n".join(
                                f"- {x['id']} ({x['criteria']}): {x['status']}. {x['evidence']}" for x in items))
    except Exception:
        pass
    return {"report_id": job_id, "summary": text, "score": score(items), "counts": _counts(items),
            "file_id": file["id"], "url": url, "download": full,
            "top_actions": [f"{p['priority']}: {p['fix']}" for p in _plan(items)[:5]]}


def latest_age_days(memory: Memory) -> int | None:
    try:
        row = memory.db.execute("SELECT max(created_at) FROM jobs WHERE kind = 'soc_report'").fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).days


COMPLIANCE_TOOL = {
    "name": "security_report",
    "description": "Buddy's SOC 2 readiness assessment (security, availability, confidentiality, privacy): every "
    "control checked against live evidence from the server, the gaps and how to fix them, as a Word document to "
    "download (also saved in Reports). Use when the user asks for a SOC report, security/compliance report, audit "
    "readiness or what to fix for security. status: the controls and today's security findings right now (for "
    "questions like 'are our backups ok?', 'is the firewall on?', 'any failed sign-ins?'), nothing saved. report "
    "(default): make the full report. How things are set up (which checks exist, how often) is in the library's "
    "Buddy Labs documents. Say plainly it's a readiness self-assessment, not a SOC 2 report: only an independent CPA "
    "firm can issue one.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["report", "status"]}}, "additionalProperties": False},
}


def status(memory: Memory) -> dict:
    ev = evidence(memory)
    items = controls(ev)
    return {"score": score(items), "summary": summary(items), "security_check": ev["check_at"][:16] or "never run",
            "check_status": ev["check_status"], "findings": [{k: x.get(k) for k in ("severity", "title", "fix")}
                                                             for x in ev["findings"][:15]],
            "facts": ev["facts"], "access_last_30_days": ev["access"],
            "recent_failed_sign_ins": ev["failures"][:5],
            "controls": [{k: c[k] for k in ("id", "control", "status", "evidence", "fix")} for c in items],
            "note": NOTICE}


def run_tool(memory: Memory, args: dict) -> str:
    if args.get("action") == "status":
        return json.dumps(status(memory), ensure_ascii=False, default=str)
    out = generate(memory)
    file_card = makers.card_for(memory, out["file_id"], "SOC 2 readiness assessment (not an audit report)")
    if file_card:
        file_card = {**file_card, "url": makers.link(out["file_id"], days=REPORT_LINK_DAYS)}
        makers._show(file_card)
    return json.dumps({k: out[k] for k in ("report_id", "summary", "score", "counts", "top_actions")}
                      | {"note": "The document is in the chat and in Reports. It's a self-assessment, not a SOC 2 "
                                 "report."}, ensure_ascii=False)
