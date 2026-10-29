# Buddy: the complete setup guide (phone only)

From nothing to Buddy talking in your earbuds, in about 90 minutes, once, done
entirely from your **Samsung Galaxy Z Fold**. No computer needed. Unfold the phone:
the big screen and keyboard make the terminal steps comfortable.

> Don't use a work (enterprise) computer for this. Company PCs are monitored and
> managed, and your personal server keys don't belong on them.

Each step ends with **Check**: what you should see before moving on. If a check
fails, stop and look at [When something goes wrong](#when-something-goes-wrong).

At the end you'll have:

- Buddy's brain on your own private server in New York, reachable only from your devices.
- The Buddy app on your Fold, connected through Tailscale, talking in your earbuds.
- An encrypted database, nightly encrypted backups, a monthly spending cap.

```
 Earbuds ─Bluetooth─ Fold (Buddy app) ─Tailscale, encrypted─ Your server (brain) ─HTTPS─ Claude
```

## What you need

| Thing | Notes |
|---|---|
| Your Galaxy Z Fold | Everything is done on it, in Chrome and one terminal app (Termux). |
| Earbuds | Shokz OpenDots ONE or any Bluetooth earbuds. |
| A password manager | Samsung Pass, Google Password Manager or Bitwarden: you'll save 3 secrets. |
| A payment card | For DigitalOcean and Anthropic. |
| About 90 minutes | Most of it is waiting for installs. |

**Copy and paste on the phone:** in Chrome, long-press text → **Copy**. In Termux,
long-press the screen → **Paste**; to copy *from* Termux, long-press, drag to select,
then tap **Copy**.

## Monthly cost

Prices are approximate list prices; confirm them when you sign up.

Buddy runs in **economy mode** by default: everyday chat on Claude Sonnet 5.5,
research and close Listen-in checks on Claude Opus 5.5, and a Claude Haiku 5.5
first pass that decides whether room speech needs a close look at all.

| Item | Choice | Per month |
|---|---|---|
| Server | DigitalOcean Basic droplet, 2 GB RAM, 1 vCPU, New York | about $12 |
| Private network | Tailscale Personal | free |
| Everyday use | About 40 chats a day, reminders, briefing, a few web searches, a quick research job every other day | about $15–25 |
| Listen-in, rooms in your languages | About $0.20 per hour of talking around you; silence is free | about $6 per daily hour |
| Listen-in, rooms in other languages (e.g. Spanish) | About $1.50 per hour, or about $0.30 with the travel settings below | $9–45 per daily hour |
| Off-server backup copy (optional) | Backblaze B2 or similar | a few cents |
| **Typical total** | Daily use plus about 2 hours of Listen-in a day | **about $40–60** |

**Buddy is thrifty and asks before spending more.** It keeps three allowances:
a daily one (your monthly budget spread over the month), one for Listen-in
($0.50 a day), and the monthly budget. When one runs out, or an action would cost
more than $0.50 (such as a deep research job), Buddy asks you first, for example:
*"We've spent $1.40 today, over the $1.29 allowance. Keep going today?"* Your
yes or no is read by Buddy's own code, never by the AI, so it can't approve its own
spending, and confirming costs nothing. A yes to more money for the month adds
`BUDDY_TOPUP_USD` ($10), up to a hard cap you set (default: twice the budget).

---

## Part A — Accounts and tools (25 minutes)

### Step 1. Claude API key

1. In Chrome, go to **console.anthropic.com**, sign up, add a payment method.
2. **Settings → Limits**: set a monthly spend limit a little above Buddy's own
   budget, for example **$50**. This is your safety net.
3. **API keys → Create key**, name it `buddy-server`, tap **Copy**, and save it in your
   password manager now as "Buddy Claude key": it is shown only once. It starts with `sk-ant-`.

**Check:** the key is saved in your password manager.

> Your Claude subscription is separate and can't power Buddy: the API key is what lets
> a server use Claude on its own.

### Step 2. Tailscale, your private network

1. Install **Tailscale** from the Play Store, open it, sign up (free Personal plan), allow the VPN.
2. In Chrome, open **login.tailscale.com/admin** → **DNS**: turn on **MagicDNS**, then
   **HTTPS Certificates**. Buddy needs HTTPS for the microphone.

**Check:** the admin page's **Machines** list shows your Fold.

### Step 3. Termux, a terminal on your phone

1. Install **F-Droid** from **f-droid.org** (Chrome will ask to allow installing it), then
   install **Termux** from F-Droid. (Avoid the Play Store version; it's outdated.)
2. Open Termux and run, one line at a time (answer `y` if asked):
   ```
   pkg update
   pkg install openssh age curl
   ssh-keygen -t ed25519
   ```
   Press Enter at each `ssh-keygen` question.
3. Show your phone's public key and copy it:
   ```
   cat ~/.ssh/id_ed25519.pub
   ```
   Long-press, select the whole line starting `ssh-ed25519`, **Copy**.

**Check:** you've copied a line starting with `ssh-ed25519`.

### Step 4. The server

1. In Chrome, go to **digitalocean.com**, sign up.
2. **Create → Droplets**:
   - Region: **New York** if it's offered, otherwise **San Francisco** or any US region.
     The region barely matters: most of Buddy's response time is Claude thinking, not
     the trip to the server. You can't move a droplet later, but you can rebuild it
     anywhere from Buddy's encrypted backup.
   - Image: **Ubuntu 24.04 (LTS) x64**
   - Size: **Basic → Regular → 2 GB / 1 CPU** (about $12)
   - Authentication: **SSH Key → New SSH Key** → paste the line from Step 3, name it "Fold"
   - Hostname: `buddy`
   - Leave **Backups** off: Buddy makes its own encrypted ones.
3. Create it and copy its **IP address**.
4. In Termux:
   ```
   ssh root@<IP>
   ```
   Type `yes` the first time.

**Check:** the prompt changes to `root@buddy:~#`. You're on your server.

---

## Part B — The brain on your server (30 minutes)

All of Part B happens in Termux, logged in to the server (`root@buddy:~#`).

### Step 5. Let the server read the code

The repo is private, so give the server its own read-only key:

```
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Copy the line it prints. In Chrome, open **github.com/aneesh304810/BuddyAgent →
Settings → Deploy keys → Add deploy key** (on the phone, Settings is under the repo's
**⋯** menu if you don't see it). Title `buddy-server`, paste the key, leave **Allow write
access off**, **Add key**.

Back in Termux:
```
ssh -T git@github.com
```

**Check:** it answers "Hi aneesh304810/BuddyAgent! You've successfully authenticated…"
(type `yes` if asked about the fingerprint).

### Step 6. Run the setup script

```
git clone --branch claude/personal-ai-voice-assistant-0zc5yh git@github.com:aneesh304810/BuddyAgent.git /tmp/buddy-setup
sudo bash /tmp/buddy-setup/deploy/bootstrap.sh git@github.com:aneesh304810/BuddyAgent.git generate claude/personal-ai-voice-assistant-0zc5yh
```

(Once the code is merged into `main`, drop the `--branch …` part and the last word.)

The script takes about 5–10 minutes and stops three times for you:

1. **Your backup key.** It prints a line starting `AGE-SECRET-KEY-`. Copy it into your
   password manager as "Buddy backup key", then type `SAVED`. It's the only way to open
   your backups, and the server deletes it right after: the server can write backups but
   never read them.
2. **A Tailscale login link.** Long-press to copy it, open it in Chrome, and approve the
   server. Tailscale on your phone must be on.
3. **Your details:** paste your Claude key from Step 1 (it stays hidden as you paste), type
   your first name, and your monthly budget (Enter = $40).

Meanwhile it turns on automatic security updates, creates a locked-down `buddy` user,
**closes every public port, including SSH**, installs Buddy with its own secrets, sets up
nightly backups, and publishes Buddy privately on your tailnet.

**Check:** it ends with `==> 7/7 Done` and prints a link like
`https://buddy.tail1234.ts.net/?token=…`. **Copy it into your password manager** as
"Buddy server link": it's your key to Buddy.

From now on, with Tailscale on, reach the server from Termux with:

```
ssh root@buddy
```

`ssh root@<IP>` stops working: that's the firewall doing its job. For emergencies,
DigitalOcean's web page always works: Droplet → **Access → Launch Droplet Console**.

### Step 7. Check it's running

```
sudo journalctl -u buddy -n 30 --no-pager
curl -s http://127.0.0.1:8000/health
```

**Check:** the log ends with `Uvicorn running on http://127.0.0.1:8000`, there's no
`BUDDY_DB_KEY is not set` warning, and the second line prints `{"ok":true}`.

**Changing settings later** (optional): `sudo nano /etc/buddy/buddy.env`, edit, save with
**Ctrl+O, Enter** (Termux shows Ctrl in the extra-keys row above the keyboard), exit with
**Ctrl+X**, then `sudo systemctl restart buddy`.

Optional, add only if you want to change the defaults:

```
BUDDY_RESEARCH_DAILY_LIMIT=10     # research jobs per day
BUDDY_RESEARCH_MAX_USD=5          # the most one research job may spend
BUDDY_MAX_PARALLEL_JOBS=2         # background jobs at the same time

BUDDY_CHAT_MODEL=claude-sonnet-5-5          # everyday chat; claude-opus-5-5 = smarter, about 2x the cost
BUDDY_LISTEN_MODEL=claude-opus-5-5          # close Listen-in checks
BUDDY_LISTEN_TRIAGE_MODEL=claude-haiku-5-5  # cheap first pass; empty = always check closely
BUDDY_LISTEN_BATCH_SECONDS=20               # gather this much room speech per check
```

Thrift settings (defaults shown):

```
BUDDY_MONTHLY_BUDGET_USD=40       # the monthly budget
BUDDY_HARD_CAP_USD=80             # never above this, even with your yes (default 2x budget)
BUDDY_TOPUP_USD=10                # what one yes adds to the month
BUDDY_DAILY_ALLOWANCE_USD=0       # 0 = monthly budget / days in the month
BUDDY_LISTEN_DAILY_USD=0.50       # Listen-in per day; a yes adds the same again
BUDDY_APPROVE_OVER_USD=0.50       # single actions above this need your yes
BUDDY_EXPERT_DAILY_LIMIT=20       # "ask the expert" escalations per day (about 5-25 cents each)
BUDDY_THINK_PHRASES=let me think, let me see, give me a second   # Listen-in cues for a whispered answer
```

**How Buddy decides to spend more.** It starts cheap and asks itself whether the
answer is good enough. If not, it steps up one level at a time: memory, web
search, the expert (a stronger model thinking harder, with web access, for high
stakes, hard maths, conflicting facts or when you push back), a quick research
job, and only with your yes a deep report. When a quick research job comes back
with low confidence, Buddy offers the deep report and waits for your answer.

**Travel settings** for long days among people speaking a language you don't
know (Listen-in drops from about $1.50 to about $0.30 per hour; whispers arrive
up to a minute later):

```
BUDDY_LISTEN_MODEL=claude-sonnet-5-5
BUDDY_LISTEN_BATCH_SECONDS=45
```

**Never change** `BUDDY_DB_KEY` (it unlocks your database) or `BUDDY_TOKEN`.
After any change: `sudo systemctl restart buddy`.

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

### Step 12 (optional). Connect your calendar and email

Buddy gets much more useful once it can see your day and your inbox. Follow
[`docs/CONNECTORS.md`](CONNECTORS.md): about 2 minutes for Calendar (a private
link) and 5 for Gmail (an app password; Buddy only reads). Then ask *"What's on my
calendar today?"* or *"Did my flight confirmation come?"*

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

Then check spending in Termux (with Tailscale on; `TOKEN` is the value after `token=` in your link):

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
ssh root@buddy
cd /opt/buddy && sudo -u buddy git pull && sudo -u buddy backend/.venv/bin/pip install -q -r backend/requirements.txt && sudo systemctl restart buddy
```

Research jobs running at that moment resume by themselves after the restart.
For the phone app, install the newest APK from Releases over the old one.

### Checking on Buddy

In Termux on your phone, with Tailscale on (`TOKEN` = the part after `token=` in your Buddy server link):

| What | Command |
|---|---|
| Is it running? | `ssh root@buddy systemctl status buddy` |
| Recent log | `ssh root@buddy journalctl -u buddy -n 50 --no-pager` |
| Claude spend this month | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/usage` |
| Research and other jobs | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/api/jobs` |
| Agents and their status | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/agents` |
| What agents did | `curl -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/audit` |
| Switch an agent off | `curl -X POST -H "Authorization: Bearer TOKEN" https://buddy.<tailnet>.ts.net/admin/agents/<name>/disable` |

### Backups

Every night at 03:15 the server writes an encrypted backup to `/var/backups/buddy/`
and keeps the last 14. Once a month, test that you can restore one:

In Termux on your phone (Tailscale on):

1. Put your backup key in a file: `nano buddy-backup.key`, paste the
   `AGE-SECRET-KEY-…` line from your password manager, save (**Ctrl+O, Enter, Ctrl+X**).
2. Copy last night's backup to the phone:
   `scp root@buddy:/var/backups/buddy/buddy-YYYYMMDD-HHMM.db.age .`
   (see the names with `ssh root@buddy ls /var/backups/buddy`)
3. Decrypt it: `age -d -i buddy-backup.key buddy-YYYYMMDD-HHMM.db.age > buddy.db`.
   If that works, your backups are good. Then delete both files from the phone:
   `rm buddy-backup.key buddy.db buddy-*.age`
4. To actually restore: the decrypted file is still encrypted with `BUDDY_DB_KEY`. Copy it
   back (`scp buddy.db root@buddy:/var/lib/buddy/buddy.db`), then on the server
   `chown buddy:buddy /var/lib/buddy/buddy.db && systemctl restart buddy`.

For a copy off the server, install `rclone`, configure a remote (for example
Backblaze B2), and add `BUDDY_BACKUP_REMOTE=b2:my-bucket/buddy` to `/etc/buddy/buddy.env`.

### If you lose your phone

1. In Chrome on any device, open the Tailscale admin console → **Machines** → the lost
   phone → **Remove**. It can no longer reach Buddy.
2. On the new phone, do Steps 2–3 (Tailscale, Termux, new SSH key). Your old phone's
   SSH key is gone, so log in once through DigitalOcean's web page: Droplet →
   **Access → Launch Droplet Console**, and run:
   ```
   echo "<paste the new phone's ssh-ed25519 line>" >> /root/.ssh/authorized_keys
   sed -i '/<old key comment, e.g. u0_a123@localhost>/d' /root/.ssh/authorized_keys
   ```
3. Give Buddy a new token: `nano /etc/buddy/buddy.env`, replace `BUDDY_TOKEN` with the
   output of `openssl rand -hex 24`, save, `systemctl restart buddy`.
4. In the Buddy app on the new phone, paste the server link with the new token.

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
