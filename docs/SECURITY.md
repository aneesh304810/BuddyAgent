# Security

## How Buddy is protected

| Layer | Protection |
|---|---|
| **Network** | No public ports. The server is reachable only inside your Tailscale network (WireGuard); public SSH is closed by the firewall. |
| **Access** | Every request needs the access token (48 random hex characters, compared in constant time). With no token set, the server refuses everyone (only `BUDDY_DEV=1` on a developer's machine opens it). Tokens are hidden from logs and removed from the browser's address bar. No public API docs. |
| **Brute-force lockout** | 10 failed sign-ins from one address within 15 minutes block it for 15 minutes (HTTP 429, app connection code 4429); each lockout is logged. |
| **Access log** | Every API, admin and bank-page call and every app connection: time, address, tailnet login, method, path (never the query string), result and duration; no content, bodies or keys. Its own encrypted database, kept 400 days; `/admin/access-log`. |
| **Security headers** | Content-Security-Policy (only Buddy's scripts, no framing), HSTS, X-Frame-Options DENY, nosniff, no referrer, a Permissions-Policy; API answers not cached. The bank pages keep their own policy for Plaid. |
| **SOC 2 readiness** | A monthly self-assessment (and on request: "generate my SOC report") maps about 35 controls to the Trust Services Criteria with live evidence, and lists the gaps; Word document in Reports. Not a SOC 2 report: only a CPA firm issues one. All checks are listed in [company/security-implementation.md](company/security-implementation.md). |
| **Access control** | Written policy with roles, quarterly access reviews with saved evidence, one-command offboarding and a daily timer that locks any server account or SSH key not on the approved list ([ACCESS_CONTROL.md](ACCESS_CONTROL.md), `access.sh`). Agents can never receive bank, database, app-token or Claude keys. |
| **Secrets** | All keys live in Buddy's settings, root only. **Sealed** (recommended, `security.sh seal`): encrypted on disk with this machine's own key (systemd-creds), with no plain copy anywhere; only Buddy's services can decrypt them, into private memory, while they run. The setup scripts decrypt to memory for a moment and re-encrypt. Agents get only the settings they declare; the code sandbox has its own token and never sees Buddy's. Keys are hidden from logs, and the weekly check looks for any that slipped into them. Every key change is dated, and keys older than 90 days get a rotation reminder. |
| **Data** | SQLCipher-encrypted database; card numbers, ID numbers, codes and passwords masked before storage or before Claude sees them; nightly backups encrypted again with your age key, which isn't on the server. |
| **The app** | Server token encrypted with the Android Keystore; no cloud backup; HTTPS only. |
| **Service** | Runs as an unprivileged `buddy` user with systemd hardening: read-only system, private /tmp, no privilege escalation, writes only to its data and backup folders, no capabilities, no devices, kernel and clock protected, other processes invisible, only normal system calls and network types (`security.sh harden`, applied by `update.sh`; check with `systemd-analyze security buddy`). |
| **Patching** | Ubuntu security updates daily, with an automatic reboot at 4:30am when one needs it. Every Sunday around 4am: Ubuntu, Tailscale and Docker updates; Buddy's Python packages with a known vulnerability upgraded to the fixed version (and rolled back automatically if Buddy doesn't come back healthy); the sandbox image rebuilt on the newest base. On GitHub: tests, `pip-audit`, `bandit` and a secret scan on every change and every Monday, and Dependabot pull requests for Python, Android, Docker and Actions dependencies. |
| **Monitoring** | The weekly check also looks at file permissions, access-token strength, key ages, leaked keys in logs, anything listening outside localhost and the tailnet, the firewall, SSH passwords, Tailscale Funnel, pending updates and reboots, backups, disk space and the service's exposure score. Its findings go into Buddy's daily health check and the morning digest in your chat. |
| **Actions** | Sending, buying, booking, cleanup and spending need your yes, checked in code, not left to the model. A yes heard through Listen-in ("Hey Buddy, yes") doesn't count, since anyone nearby could say it: tap Approve or answer in Talk mode. |
| **Untrusted content** | Emails, notifications, web pages, imports and bank descriptions are labelled as information, never instructions. Rules that change your mail run only after you approved them. |
| **Built agents** | Code from the agent builder must pass an import allowlist, banned calls and attributes (files, processes, the interpreter), a host check and a second model's review. It runs only after your yes, as its own process with `python -I`, an empty home folder, only its declared settings, and approval for anything that changes things. This is a strong filter, not a perfect sandbox: read what Buddy says an agent does before switching it on. |
| **Uploads** | Life-log files are capped at 150 MB, zips at 400 MB unpacked and 5,000 files, and read without unpacking to disk. |
| **Outbound links** | Buddy fetches only `https://` links. |

## The daily threat watch

Every day Buddy checks what is newly published about security against what this server actually runs, and the
Buddy app's **Security** page (menu, 🛡 Security) shows the result.

- **What is checked.** Every Python package Buddy has installed, by exact version, against OSV (the open
  vulnerability database, which includes the GitHub and PyPI advisories). CISA's list of vulnerabilities being
  used in real attacks: a finding on it is raised to critical, and new entries about the software the server
  is built on (Linux, OpenSSH, OpenSSL, Docker, Tailscale, Python, SQLite, sudo, the phone's Android) are shown
  even when the exact version can't be matched. EPSS, the chance a vulnerability is exploited in the next 30
  days. Ubuntu security updates that are waiting. The weekly root check's findings (firewall, SSH, keys,
  backups, exposure) and Buddy's own settings (the access token's strength, database encryption, developer
  mode).
- **The page.** The setup is drawn layer by layer: your phone and the app, the network edge, the server, Buddy
  itself, the parts that read outside content, data and keys, the code sandbox, outside services. Each layer
  shows its level (the most serious open finding there), how many are open and fixed, and how many defences
  are on; tap a layer to drill in. Charts show open findings by severity and what was found and fixed each
  week. Every finding says why it matters, how it could be used against this server (from the published
  attack details and from where that software sits in Buddy), and the fix.
- **Outside services.** The Services tab (and the Outside services layer) lists every service Buddy is
  connected to: what it is for, whether it can see personal data or public data only, the addresses it
  reaches, its calls and failures in the last 7 days and when it was last used. The daily scan checks each
  provider against the public list of reported data breaches (Have I Been Pwned; nothing is sent) and the
  exploited-flaws list, and flags a key that was suddenly refused or a key for a service not used in 60 days.
  **Disconnect** stops Buddy sending anything to a service at once (also by asking Buddy: "disconnect
  Plivo"); its key stays stored, so after a breach also make a new key at the provider. Reconnect undoes it.
  Claude can't be disconnected (it is Buddy's thinking), and data the phone sends in is switched off in the app.
  A published breach is about that company's customers: it doesn't by itself mean your key or data was
  taken, and a breach nobody has published is not on any list.
- **The database** has its own box in the diagram: one encrypted file for everything personal, a vault for
  private documents, and a separate plain file that holds market and deal prices only.
- **Open and fixed.** A finding a later scan no longer finds is marked fixed, with the date. If a database
  can't be reached, nothing from it is marked fixed. You can accept a risk with a reason; it stays listed.
- **Patching.** Packages with a published fix are upgraded by the weekly root job, and rolled back if Buddy
  doesn't come back healthy. "Patch now" (ask Buddy) runs that job at once after your yes: Buddy leaves an
  empty request file and a root systemd unit runs the job; Buddy itself never runs as root.
- **Alerts.** Only for a new critical or high finding, or one being exploited now.
- **What leaves the server.** Package names and versions (to OSV) and CVE ids (to EPSS). Nothing personal.
- **Limits.** This lowers the risk; it can't prove the server is safe. A flaw nobody has published yet is in
  no database, and from inside Buddy the exact version of every operating-system package can't be seen:
  that is why Ubuntu's own automatic security updates stay on.

Ask Buddy: "security report", "any vulnerabilities?", "scan now", "explain finding 3", "patch now".

## Review of 15 November 2026

Scanned before the threat watch was added: no known vulnerability in any dependency (pip-audit), no medium or
high finding in the code scan (bandit), and the endpoints without a sign-in were reviewed one by one: file
links (signed, expiring), the phone-call path (random token and the carrier's signature), the bank and service
sign-in returns (one-time state), and the health check (no data). Fixed in this review: the security alert
that newly exploited flaws need was missing, and a table-name clash that the test suite caught before release.

## Checking it yourself

```
sudo bash /opt/buddy/deploy/security.sh check          # patch now and check everything (also runs weekly)
sudo bash /opt/buddy/deploy/security.sh seal           # encrypt your keys at rest (do this once)
sudo bash /opt/buddy/deploy/security.sh rotate-token   # new app access token; prints the new link
sudo bash /opt/buddy/deploy/update.sh                  # newest code and package fixes
```
The log of weekly runs is `/var/log/buddy-maintain.log`; the last report is `/var/lib/buddy/security.json`.

## Keys: least privilege and rotation

Give each key only what Buddy needs, so a leaked key can do little:

| Key | Make it like this |
|---|---|
| Claude (`ANTHROPIC_API_KEY`) | A key in its own workspace with a monthly **spend limit** in the Claude Console. |
| DigitalOcean (`BUDDY_DO_TOKEN`) | A custom-scope token with **read** access to billing only, not full access. |
| GitHub, for the sandbox | Fine-grained, **only the repos** Buddy may use, Contents read/write, with an expiry date. |
| Gmail | App passwords per account (revocable one by one in your Google account). |
| Free models, Serper, OpenRouter | Free tiers with no card attached, or a credit limit of 0. |
| SimpleFIN | Its access URL is read-only by design. |

**Rotate** any key that was ever pasted into a chat, email or screenshot, and the rest every 90
days (the weekly check reminds you): make a new key at the provider, save it with
`sudo bash /opt/buddy/deploy/set.sh NAME`, check Buddy works, then delete the old key at the
provider. For the app's access token: `security.sh rotate-token`.

## Review of 29 October 2026

Tools: `pip-audit` (dependencies) and `bandit` (code), plus a manual review.

| Finding | Severity | Fixed |
|---|---|---|
| Access token written to server logs (WebSocket address carries `?token=`) | High | Hidden in logs; removed from the browser address bar |
| Server open to anyone if `BUDDY_TOKEN` was missing | High | Fails closed |
| Settings file readable by the `buddy` user, so by any agent process | High | Root only (600) |
| Agent builder checks could be bypassed (`io.open`, `os.path.os`, private attributes, `mcp` internals); generated code ran once before your yes | High | Much stricter checks; runs only after approval, isolated folder |
| "Hey Buddy, yes" from someone else in the room could approve an action | Medium | Room speech can't approve |
| Large uploads read fully into memory; zip bombs | Medium | Streaming size limit; unpacked size limits |
| `pip` 24.0 and `setuptools` 79 with published advisories (installer tools) | Medium | Upgraded on every update |
| URL fetches accepted any scheme | Low | https only |
| Public `/docs` and `/openapi.json` | Low | Removed |
| bandit B608 "SQL injection" (8 places) | — | False positives: only column names from code are formatted; values are always parameters |
| Buddy's own libraries (`anthropic`, `fastapi`, `starlette`, `mcp`, `httpx`...) | — | No known vulnerabilities; minimum safe versions pinned |

## Review of 30 October 2026

| Finding | Severity | Fixed |
|---|---|---|
| Keys stored as a plain (root-only) file on disk | Medium | Encrypted at rest with systemd-creds (`security.sh seal`) |
| A key in an error message could be written to the logs | Medium | Every log line passes a filter that hides keys; weekly scan of the logs |
| Python packages patched only when you ran update.sh | Medium | Weekly automatic patching of vulnerable packages, with rollback |
| Updates that need a reboot waited indefinitely | Low | Automatic reboot at 4:30am when needed |
| Google Trends feed parsed with Python's standard XML parser | Low | `defusedxml` |
| The sandbox service would have run code the `buddy` user could change | High | Runs a root-owned copy |
| No record of key age; no reminder to rotate | Low | Key changes dated; 90-day reminder |
| Service hardening could be stricter (capabilities, devices, system calls, namespaces) | Low | Extra lockdown drop-in, removed automatically if Buddy won't start with it |
| No security checks on GitHub; no dependency update alerts | Low | Security workflow and Dependabot |
| Git history scanned for keys | — | None found (only fake keys in tests) |
| `pip-audit` on Buddy's requirements | — | No known vulnerabilities |

## Report a problem
Open an issue in the repository, or tell Buddy to pause an agent (*"pause the gmail agent"*).
