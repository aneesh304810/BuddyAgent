# Buddy: the complete setup guide

From nothing to Buddy talking in your earbuds, in about 90 minutes, once.
Each step ends with **Check**: what you should see before moving on. If a
check fails, stop and look at [When something goes wrong](#when-something-goes-wrong).

At the end you'll have:

- Buddy's brain on your own private server in New York, reachable only from your devices.
- The Buddy app on your Galaxy, connected through Tailscale, talking in your earbuds.
- An encrypted database, nightly encrypted backups, a monthly spending cap.
- The research agent working in the background and reporting to your phone.

```
 Earbuds ─Bluetooth─ Galaxy (Buddy app) ─Tailscale, encrypted─ Your server (brain) ─HTTPS─ Claude
```

## What you need

| Thing | Notes |
|---|---|
| A laptop | Windows, Mac or Linux. Used for setup only. |
| Your Samsung Galaxy and earbuds | Shokz OpenDots ONE or any Bluetooth earbuds. |
| A payment card | For DigitalOcean and Anthropic. |
| About 90 minutes | Most of it is waiting for installs. |

## Monthly cost

Prices are approximate list prices; confirm them when you sign up.

| Item | Choice | Per month |
|---|---|---|
| Server | DigitalOcean Basic droplet, 2 GB RAM, 1 vCPU, New York | about $12 |
| Private network | Tailscale Personal | free |
| Claude API | Pay per use; Buddy stops itself at your budget (default $40) | about $10–30 for daily chat |
| Research jobs | About $0.20–0.50 quick, $1–3 deep; daily limit 10 | depends on use |
| Off-server backup copy (optional) | Backblaze B2 or similar | a few cents |
| **Total** | | **about $25–50** |

---

## Part A — Accounts (20 minutes)

### Step 1. Claude API key

1. Go to **console.anthropic.com**, sign up, add a payment method.
2. **Settings → Limits**: set a monthly spend limit a little above Buddy's own
   budget, for example **$50**. This is your safety net.
3. **API keys → Create key**, name it `buddy-server`. Copy it into your password
   manager now: it is shown only once. It starts with `sk-ant-`.

**Check:** the key is saved in your password manager.

> Your Claude Max subscription is separate and can't power Buddy: the API key is
> what lets a server use Claude on its own.

### Step 2. Tailscale, your private network

1. Go to **tailscale.com**, sign up (free Personal plan). Use the same login everywhere below.
2. On your **Galaxy**: install **Tailscale** from the Play Store, open it, log in, allow the VPN.
3. On your **laptop**: install Tailscale from tailscale.com/download and log in.
4. In the Tailscale admin console (login.tailscale.com/admin), open **DNS**:
   turn on **MagicDNS**, then **HTTPS Certificates**. Buddy needs HTTPS for the microphone.

**Check:** the admin console's **Machines** page lists your phone and laptop.

### Step 3. Backup key (stays with you, never on the server)

On your laptop, install **age** (github.com/FiloSottile/age → Releases; on a Mac
`brew install age`, on Windows `winget install FiloSottile.age`). Then in a terminal:

```
age-keygen -o buddy-backup.key
```

It prints a line `Public key: age1…`. Copy that **public key**; you'll paste it in Step 6.
Save the file `buddy-backup.key` in your password manager: it is the only way to
open your backups. The server can write backups but never read them.

**Check:** you have an `age1…` public key and the key file is safe.

### Step 4. The server

1. Go to **digitalocean.com**, sign up.
2. **Create → Droplets**:
   - Region: **New York** (closest to both the US and South America)
   - Image: **Ubuntu 24.04 (LTS) x64**
   - Size: **Basic → Regular → 2 GB / 1 CPU** (about $12)
   - Authentication: **SSH Key** → add your laptop's public key
     (no key yet? run `ssh-keygen -t ed25519` on the laptop, then paste the contents of `~/.ssh/id_ed25519.pub`)
   - Hostname: `buddy`
   - Leave **Backups** off: Buddy makes its own encrypted ones.
3. Create it and copy its **IP address**.

**Check:** on your laptop, `ssh root@<IP>` logs you in.

---

## Part B — The brain on your server (30 minutes)

### Step 5. Let the server read the code

The repo is private, so give the server a read-only key. Logged in to the server:

```
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

In GitHub, open **aneesh304810/BuddyAgent → Settings → Deploy keys → Add deploy key**.
Title `buddy-server`, paste the key, leave **Allow write access off**, save.

**Check:** on the server, `ssh -T git@github.com` answers
"Hi aneesh304810/BuddyAgent! You've successfully authenticated…" (type `yes` if asked about the fingerprint).

### Step 6. Run the setup script

Still on the server. Replace `age1…` with your public key from Step 3:

```
git clone --branch claude/personal-ai-voice-assistant-0zc5yh git@github.com:aneesh304810/BuddyAgent.git /tmp/buddy-setup
sudo bash /tmp/buddy-setup/deploy/bootstrap.sh git@github.com:aneesh304810/BuddyAgent.git age1… claude/personal-ai-voice-assistant-0zc5yh
```

(Once the code is merged into `main`, drop the `--branch …` part and the last argument.)

The script takes about 5 minutes. Partway through it prints a **Tailscale login link**:
open it on your laptop and approve the server. It then:

- turns on automatic security updates
- creates a locked-down `buddy` user
- **closes every public port, including SSH**
- installs Buddy, generates its secrets, and sets up nightly backups
- publishes Buddy privately at `https://buddy.<your-tailnet>.ts.net`

**Check:** the script ends with `==> 7/7 Done` and prints a link like
`https://buddy.tail1234.ts.net/?token=…`. **Save that link in your password manager**:
it's your key to Buddy.

From now on, connect to the server with:

```
tailscale ssh root@buddy
```

Plain `ssh root@<IP>` stops working: that's the firewall doing its job.
For emergencies, DigitalOcean's web console (Droplet → Access → Launch Console) always works.

### Step 7. Your details and the Claude key

```
sudo nano /etc/buddy/buddy.env
```

Change these lines:

| Setting | Set it to |
|---|---|
| `ANTHROPIC_API_KEY` | your `sk-ant-…` key from Step 1 |
| `BUDDY_USER_NAME` | `Aneesh` |
| `BUDDY_MONTHLY_BUDGET_USD` | your monthly cap, e.g. `40` |
| `BUDDY_TIMEZONE` | leave it; Buddy follows your phone's time zone as you travel |

Optional, add only if you want to change the defaults:

```
BUDDY_RESEARCH_DAILY_LIMIT=10     # research jobs per day
BUDDY_RESEARCH_MAX_USD=5          # the most one research job may spend
BUDDY_MAX_PARALLEL_JOBS=2         # background jobs at the same time
```

**Never change** `BUDDY_DB_KEY` (it unlocks your database) or `BUDDY_TOKEN`.
Save with **Ctrl+O, Enter**, exit with **Ctrl+X**. Then:

```
sudo systemctl restart buddy
sudo journalctl -u buddy -n 30 --no-pager
```

**Check:** the log ends with `Uvicorn running on http://127.0.0.1:8000` and shows
no `BUDDY_DB_KEY is not set` warning. Also run:

```
curl -s http://127.0.0.1:8000/health
```

It should print `{"ok":true}`.

---

## Part C — Buddy on your Galaxy (15 minutes)

### Step 8. Install the app

1. On the Galaxy, make sure **Tailscale is on** (key icon in the status bar).
   Android allows one VPN at a time, so turn off any other VPN.
2. In Chrome, log in to github.com, open **aneesh304810/BuddyAgent → Releases**,
   and open the newest **Buddy for Android**.
3. Tap **buddy-0.3.N.apk**. When Android asks, allow **Chrome** to install apps, then **Install**.
   If Play Protect warns about an unknown app, choose **Install anyway**: it's your own app.

**Check:** a blue-circle **Buddy** icon is on your home screen.

### Step 9. Connect it to your server

1. Open Buddy. It opens on **Settings**.
2. Paste the link from Step 6 (`https://buddy.<tailnet>.ts.net/?token=…`) into **Server link**.
3. Tap **Save and connect**. Allow the **microphone** and **notifications**.

**Check:** the header shows a green dot and **online**, and a "Buddy" notification appears.

### Step 10. Keep Buddy awake (important on Samsung)

1. In Buddy, open **Settings** again and tap **Allow Buddy to run in the background** → **Allow**.
2. Galaxy **Settings → Battery → Background usage limits → Never sleeping apps → +** → choose **Buddy**.
3. Galaxy **Settings → Apps → Buddy → Battery → Unrestricted**.

**Check:** lock the phone for 5 minutes, unlock it: Buddy still shows **online**.

### Step 11. Earbuds

1. Pair your earbuds with the Galaxy in **Settings → Connections → Bluetooth**.
2. If your earbuds have a companion app (the Shokz app), set a single tap to **Play/Pause**.
3. Open Buddy once more so it takes the earbud button.

**Check:** tap the orb and say "Hey, can you hear me?" The answer plays in your earbuds.
Then tap your earbud instead of the orb and speak again.

---

## Part D — Test everything (15 minutes)

Do these in order. Each one checks a different part of Buddy.

| # | Do this | You should get |
|---|---|---|
| 1 | Say "Remember that my favorite coffee is a flat white." Then: "What's my favorite coffee?" | It remembers. |
| 2 | Say "Remind me in 2 minutes to stretch." Lock the phone. | A spoken reminder and a notification, screen off. |
| 3 | Switch to **Listen-in**, pick **Spanish (Latin America)**, play a Spanish YouTube clip nearby. | Whispered gist of what's said. |
| 4 | Say "Buddy, what time is it in São Paulo?" while in Listen-in. | It answers the command directly. |
| 5 | Call yourself from another phone, or start a WhatsApp call. | The status says "Paused while you're on a call", then it resumes after. |
| 6 | Say "Do a quick research: the best eSIM for a month in Brazil." | "Started…" right away. A few minutes later: a spoken summary and a notification. |
| 7 | Tap that notification. | The full report with sources opens in Buddy. |
| 8 | Say "What were the prices in that eSIM report?" | It answers from the report. |
| 9 | Tap **Reports** in the header. | Your research history. |
| 10 | Type "test" in the box at the bottom and tap **Send**. | A reply, without speaking. |

Then check spending on your laptop (with Tailscale on; `TOKEN` is the value after `token=` in your link):

```
curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/usage
```

**Check:** a few cents so far, and `monthly_budget_usd` shows your cap.

---

## Part E — Living with Buddy

### Everyday use

- **Talk:** tap your earbud or the orb. Talk over Buddy to interrupt.
- **Research:** "Research…" or "Analyze…". Say "deep" for a full report; Buddy asks before a deep job, since those cost a dollar or more.
- **Ask about past research:** "What did that laptop report say?"
- **Cancel:** "Cancel the research job."
- **Agents:** "Buddy, pause the worldclock agent" switches an agent off instantly.

### Updating Buddy

When new code is pushed:

```
tailscale ssh root@buddy
cd /opt/buddy && sudo -u buddy git pull && sudo -u buddy backend/.venv/bin/pip install -q -r backend/requirements.txt && sudo systemctl restart buddy
```

Research jobs running at that moment resume by themselves after the restart.
For the phone app, install the newest APK from Releases over the old one.

### Checking on Buddy

From any device on your tailnet (`TOKEN` = your `BUDDY_TOKEN`):

| What | Command |
|---|---|
| Is it running? | `tailscale ssh root@buddy systemctl status buddy` |
| Recent log | `tailscale ssh root@buddy journalctl -u buddy -n 50 --no-pager` |
| Claude spend this month | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/usage` |
| Research and other jobs | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/api/jobs` |
| Agents and their status | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/agents` |
| What agents did | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/audit` |
| Switch an agent off | `curl -X POST -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/agents/<name>/disable` |

### Backups

Every night at 03:15 the server writes an encrypted backup to `/var/backups/buddy/`
and keeps the last 14. Once a month, test that you can restore one:

1. Copy one to your laptop (Tailscale on):
   `scp root@buddy:/var/backups/buddy/buddy-YYYYMMDD-HHMM.db.age .`
2. Decrypt it: `age -d -i buddy-backup.key buddy-YYYYMMDD-HHMM.db.age > buddy.db`
3. The result is still encrypted with `BUDDY_DB_KEY`. To restore, copy it to
   `/var/lib/buddy/buddy.db` on the server (owner `buddy`) and restart Buddy.

For a copy off the server, install `rclone`, configure a remote (for example
Backblaze B2), and add `BUDDY_BACKUP_REMOTE=b2:my-bucket/buddy` to `/etc/buddy/buddy.env`.

### If you lose your phone

1. Tailscale admin console → **Machines** → the phone → **Remove**. It can no longer reach Buddy.
2. On the server, give Buddy a new token: edit `BUDDY_TOKEN` in `/etc/buddy/buddy.env`
   (any long random text, e.g. from `openssl rand -hex 24`), then `systemctl restart buddy`.
3. On the new phone, paste the link with the new token.

---

## When something goes wrong

| Problem | Fix |
|---|---|
| App says **offline** | Is Tailscale on (key icon)? Another VPN on? On the server: `systemctl status buddy`. |
| App says **wrong token** | Paste the full link again; the token must match `BUDDY_TOKEN` on the server. |
| Link won't open: "site can't be reached" | Tailscale off on the phone, or HTTPS Certificates not turned on (Step 2). On the server: `tailscale serve status`. |
| Buddy replies "I can't reach my brain" | API key wrong or out of credit: check `ANTHROPIC_API_KEY` and console.anthropic.com → Billing. |
| Buddy says the AI budget is used up | Raise `BUDDY_MONTHLY_BUDGET_USD`, restart, or wait for the next month. |
| Stops working with the screen off | Repeat Step 10; Samsung updates sometimes reset these. |
| Earbud tap does nothing | Open Buddy again after playing music elsewhere; set the earbud's tap to Play/Pause. |
| Research never reports back | `curl …/api/jobs` shows its status and error. Daily limit reached? Raise `BUDDY_RESEARCH_DAILY_LIMIT`. |
| "App not installed" when updating the APK | The build signing key changed: uninstall Buddy, install the new APK, paste your link again. |
| Locked out of the server | DigitalOcean → Droplet → **Access → Launch Droplet Console**. |
| Service won't start after editing the env file | `journalctl -u buddy -n 50` shows the line; usually a typo or a missing value. |

---

## What protects your privacy

| Layer | Protection |
|---|---|
| Network | No public ports. Only your Tailscale devices can reach the server. |
| Transport | Phone ↔ server is encrypted twice: Tailscale (WireGuard) and HTTPS. |
| Database | Encrypted with SQLCipher; unreadable on disk without `BUDDY_DB_KEY`. |
| Backups | Encrypted again with your `age` key, which never touches the server. |
| Claude | Card, account and routing numbers; US SSN/ITIN; South American IDs (CPF, CNPJ, RUT, CUIT, DNI, cédula); Aadhaar and PAN; passports; one-time codes, CVVs and passwords are masked before any text leaves the server. Research questions and data are masked the same way. |
| Phone | The server token is encrypted with an Android Keystore key; no cloud backup; no access to contacts, calls, SMS or location. |
| Secrets | In `/etc/buddy/buddy.env`, readable only by root and the Buddy service. |
| Service | Runs as an unprivileged user that can write only its own data and backup folders. |
| Agents | Least access, approvals, daily limits, audit log, kill switch. |
| Spending | Buddy's monthly budget, a per-job research cap, a daily research limit, and Anthropic's own spend limit. |
| Updates | Security updates install automatically. |

The limit: the hosting company controls the hardware. While Buddy runs, the
database key is in the server's memory, so someone with full control of the
machine could in principle read it. Encryption protects against stolen disks,
snapshots and backups, not against the host itself.
