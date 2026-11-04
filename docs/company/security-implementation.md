# How Buddy is secured: implementation and checks

Buddy Labs · internal reference · updated 2026-11-04

This is the plain-language reference Buddy answers from when asked "how is Buddy secured?",
"what security checks do we have?", "how often do they run?" or "where is the evidence?".
Live results (today's findings, the readiness score) come from the security check and the SOC
readiness report. This document describes how things are set up, not how they are doing today.

## 1. Architecture and boundaries

- **One private server** (DigitalOcean, Ubuntu). Buddy's service listens only on `127.0.0.1:8000`.
- **Reached only over Tailscale**, the owner's private WireGuard network. `tailscale serve` publishes
  it to tailnet devices with HTTPS (Tailscale certificates). There are no public ports, and Tailscale
  Funnel (public exposure) is off and checked weekly.
- **Firewall (ufw)**: denies all incoming traffic except on the tailnet interface. Public SSH is closed.
- **Clients**: the Buddy Android app (server token encrypted with the Android Keystore, HTTPS only,
  no cloud backup) and Buddy's web page on the tailnet.
- **Providers** (subservice organizations): DigitalOcean (hosting), Tailscale (network), Anthropic
  (Claude), Plaid (read-only banks), Groq (speech to text), Cloudflare (website and email routing,
  no customer data), GitHub (code and CI).

## 2. Identity, authentication and access control

| Control | How it is implemented |
|---|---|
| App and API sign-in | Every API call, admin call and app connection needs the access token (48 random hex characters), compared in constant time. With no token set the server refuses everyone. |
| Brute-force lockout | 10 failed sign-ins from one network address within 15 minutes block that address for 15 minutes (HTTP 429; app connection closed with code 4429). Every lockout is written to the access log. |
| Device access | New tailnet devices must be approved by the owner; the Google account behind Tailscale uses 2-Step Verification; the DigitalOcean account uses an authenticator app. |
| Admin access to the server | SSH keys or Tailscale SSH (re-check every 12 hours); password login off (checked weekly). |
| Roles (RBAC) | Tailnet policy (`deploy/tailscale-policy.hujson`): owner/admin may reach the server; the server is tagged `tag:buddy-server`. Buddy runs as an unprivileged `buddy` user; the code sandbox as `buddy-sandbox`. |
| Approved access list | `/etc/buddy/access-approved.json`: the people, SSH keys, sudoers and tailnet devices that may have access. |
| Automated deprovisioning | A daily timer (`buddy-access.timer`, `access.sh enforce`) locks any server account or SSH key that is not on the approved list. `access.sh offboard NAME` removes a person's accounts, keys and devices in one step. |
| Periodic access review | Every 90 days: `sudo bash /opt/buddy/deploy/access.sh review`. Each review is saved as evidence in `/var/lib/buddy/access-reviews/`. The weekly check warns when one is due. |
| Least privilege for add-ons | Connectors and add-on agents never receive core keys: bank (Plaid, SimpleFIN), database key, access token, Claude key, DigitalOcean and Tailscale keys. |
| Actions need a yes | Sending, buying, booking, cleanup and spending need the owner's yes, checked in code. A yes heard through Listen-in doesn't count. Bank access is read-only: Buddy cannot pay or move money. |

## 3. Data protection

| Control | How it is implemented |
|---|---|
| Database encryption | SQLCipher (AES-256) with a 64-hex-character key (`BUDDY_DB_KEY`). |
| Uploaded files | Encrypted with AES-256-GCM, with a key derived from the database key. Sensitive documents go in a vault: Buddy asks before an AI reads them again. |
| Keys and secrets | Entered only through hidden prompts (`set.sh NAME`, `security.sh NAME`), never in chat. Sealed on disk with systemd-creds (`security.sh seal`); only Buddy's services can decrypt them, into memory. Every key change is dated; rotation reminder after 90 days. |
| Logs | Keys and tokens are masked in every log line (SecretFilter). The phone's `?token=` is hidden from server logs. The weekly check scans the last 7 days of logs for anything that looks like a key. |
| Masking | Card numbers, ID numbers, codes and passwords are masked before storage or before Claude sees them. Only the card name and last 4 digits are kept. |
| In transit | HTTPS (TLS 1.2+) everywhere; phone to server also inside WireGuard. HSTS is sent on every page. |
| Backups | Nightly (`buddy-backup.timer`), encrypted with an age key that is not on the server; the last 14 are kept. Restore with `deploy/restore.sh`. |
| Retention | Fixed periods, deleted automatically: access log 400 days, money reports 12 to 24 months, emails 30 days, room transcripts 2 days, diagnostics 30 days, AI usage records 90 days, SOC readiness reports 24 months. The full table is in the privacy policy. |

## 4. Application security

- **Security headers on every page**: Content-Security-Policy (only Buddy's own scripts run;
  nothing can frame Buddy's pages; no plugins), Strict-Transport-Security, X-Frame-Options DENY,
  X-Content-Type-Options nosniff, Referrer-Policy no-referrer, Cross-Origin-Opener-Policy and a
  Permissions-Policy (no camera, location, payment or USB). The bank-connection pages carry their own
  policy that allows only Plaid's script. API answers are marked no-store, so they aren't cached.
- **No public API documentation** (no /docs or /openapi.json).
- **Untrusted content** (emails, notifications, web pages, bank descriptions, uploaded documents) is
  labelled as information, never instructions.
- **Signed download links**: documents Buddy makes are served at `/api/files/...` with an HMAC
  signature that expires (30 days; SOC readiness reports 365 days).
- **Code sandbox**: code from the internet runs only in throwaway Docker containers run by a separate
  service: not root, no capabilities, read-only system, CPU/memory/time limits, no access to Buddy's
  files, keys, database, tailnet or private networks.
- **Built agents**: import allowlist, banned calls, host check, a second model's review, the owner's yes,
  and a separate process with only its declared settings.
- **Service hardening (systemd)**: unprivileged user, read-only system, private /tmp, no new
  privileges, no capabilities, no devices, kernel and clock protected, other processes invisible,
  system-call filter (`security.sh harden`; exposure score checked weekly, under 5 is good).

## 5. Logging and monitoring

- **Access log** (`guard.py`): every API, admin and bank-page request and every app connection,
  with time, network address, tailnet login (when Tailscale passes one), method, path (never the
  query string), result and duration. Never message content, bodies or keys. Its own encrypted
  database file (`access-log.db`), kept 400 days. The owner can see it at `/admin/access-log`
  (and `?failures=true` for failed sign-ins and lockouts).
- **Daily health check**: service health, model errors, costs, and the weekly security findings,
  summarised in the morning digest.
- **Agent audit log**: every tool call made by add-on agents (`/admin/audit`).

## 6. Catalogue of checks

| Check | When | What it looks at | Where the results go |
|---|---|---|---|
| Weekly security check and patching (`maintain.py`, `buddy-maintain.timer`) | Sundays around 4am, or `sudo bash /opt/buddy/deploy/security.sh check` | Ubuntu, Tailscale and Docker updates; vulnerable Python packages upgraded (rolled back if Buddy isn't healthy after); sandbox image rebuilt; key encryption, permissions, token strength, key ages; leaked keys in logs; anything listening outside localhost and the tailnet; firewall; SSH passwords; Tailscale Funnel; automatic updates; reboot needed; pending security updates; backups in the last 2 days; disk space; service exposure score; Graphviz; the access-control check below | `/var/lib/buddy/security.json` (findings and facts), `/var/log/buddy-maintain.log`, the daily digest, the SOC readiness report |
| Access-control check (`access.py`) | Inside the weekly check | Accounts, SSH keys, sudoers, tailnet devices and users against the approved list; devices not seen for 60 days; review due | The weekly findings |
| Access enforcement (`buddy-access.timer`) | Daily | Locks accounts and SSH keys not on the approved list | `/var/log/buddy-access.log` |
| Quarterly access review (`access.sh review`) | Every 90 days, by the owner | Everything with access, approved or not | `/var/lib/buddy/access-reviews/` |
| Automatic OS security updates (unattended-upgrades) | Daily, reboot at 4:30am if needed | Ubuntu security patches | apt logs |
| Brute-force lockout | Every request | Failed sign-ins per address | Access log |
| CI security checks (GitHub Actions `security.yml`) | Every change and every Monday | Tests, pip-audit (known vulnerabilities), bandit (unsafe code), gitleaks (secrets in the repo) | GitHub Actions |
| Dependabot | Continuous | Python, Android, Docker and Actions dependencies | Pull requests |
| App build (`android.yml`) | Every change | The Android app compiles and its checks pass | GitHub Actions |
| Backups (`buddy-backup.timer`) | Nightly | Encrypted copy of the database, its key and uploaded files | `/var/backups/buddy` |
| SOC 2 readiness assessment (`compliance.py`) | Monthly (automatic), or on request ("generate my SOC report") | About 35 controls mapped to the Trust Services Criteria, with live evidence, gaps and a remediation plan | Reports (Security) and a Word document to download |

## 7. SOC 2 readiness

Buddy Labs follows SOC 2 practices and is preparing for an examination. The monthly SOC readiness
assessment maps Buddy's controls to the AICPA Trust Services Criteria (Security CC1 to CC9,
Availability A1, Confidentiality C1, Privacy P1 to P8) with evidence read live from the server.
It is a self-assessment, not a SOC 2 report: only an independent licensed CPA firm can examine the
controls and issue a SOC 2 Type I or Type II report. Until then, never say "SOC 2 compliant" or
"SOC 2 certified".

What only the owner can provide, kept on file: the yearly signed Information Security Program,
security awareness training, vendor SOC 2 reports (DigitalOcean, Tailscale, Anthropic, Plaid,
Cloudflare, GitHub), a yearly restore test, a yearly incident-response tabletop exercise, and a log of
privacy requests.

## 8. Incidents and contacts

- Report a security problem: security@buddylabs.io (also in `https://buddylabs.io/.well-known/security.txt`).
- Privacy questions: privacy@buddylabs.io. The auto-reply promises an answer within 3 business days.
- The incident response plan (contain, assess, notify within legal deadlines, recover, learn) is part
  of the Information Security Program.

## 9. Owner commands

```
sudo bash /opt/buddy/deploy/security.sh check          # patch now and run every check
sudo bash /opt/buddy/deploy/security.sh seal           # encrypt keys at rest (once)
sudo bash /opt/buddy/deploy/security.sh rotate-token   # new app access token
sudo bash /opt/buddy/deploy/set.sh NAME                # save or replace a key (hidden prompt)
sudo bash /opt/buddy/deploy/access.sh review           # quarterly access review
sudo bash /opt/buddy/deploy/access.sh offboard NAME    # remove a person's access
sudo bash /opt/buddy/deploy/update.sh                  # newest code and fixes
```
