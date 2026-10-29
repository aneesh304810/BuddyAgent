# Security

## How Buddy is protected

| Layer | Protection |
|---|---|
| **Network** | No public ports. The server is reachable only inside your Tailscale network (WireGuard); public SSH is closed by the firewall. |
| **Access** | Every request needs the access token (48 random hex characters, compared in constant time). With no token set, the server refuses everyone (only `BUDDY_DEV=1` on a developer's machine opens it). Tokens are hidden from logs and removed from the browser's address bar. No public API docs. |
| **Secrets** | All keys live in `/etc/buddy/buddy.env`, readable by root only (systemd hands Buddy its settings). Agents get only the settings they declare. |
| **Data** | SQLCipher-encrypted database; card numbers, ID numbers, codes and passwords masked before storage or before Claude sees them; nightly backups encrypted again with your age key, which isn't on the server. |
| **The app** | Server token encrypted with the Android Keystore; no cloud backup; HTTPS only. |
| **Service** | Runs as an unprivileged `buddy` user with systemd hardening (read-only system, private /tmp, no privilege escalation, writes only to its data and backup folders). |
| **Actions** | Sending, buying, booking, cleanup and spending need your yes, checked in code, not left to the model. A yes heard through Listen-in ("Hey Buddy, yes") doesn't count, since anyone nearby could say it: tap Approve or answer in Talk mode. |
| **Untrusted content** | Emails, notifications, web pages, imports and bank descriptions are labelled as information, never instructions. Rules that change your mail run only after you approved them. |
| **Built agents** | Code from the agent builder must pass an import allowlist, banned calls and attributes (files, processes, the interpreter), a host check and a second model's review. It runs only after your yes, as its own process with `python -I`, an empty home folder, only its declared settings, and approval for anything that changes things. This is a strong filter, not a perfect sandbox: read what Buddy says an agent does before switching it on. |
| **Uploads** | Life-log files are capped at 150 MB, zips at 400 MB unpacked and 5,000 files, and read without unpacking to disk. |
| **Outbound links** | Buddy fetches only `https://` links. |

## Checking it yourself

```
sudo bash /opt/buddy/deploy/audit.sh     # known vulnerabilities in installed packages, file permissions, firewall
sudo bash /opt/buddy/deploy/update.sh    # newest code and package fixes
```
Automatic security updates for Ubuntu are on (`unattended-upgrades`).

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

## Report a problem
Open an issue in the repository, or tell Buddy to pause an agent (*"pause the gmail agent"*).
