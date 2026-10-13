# Setting up Buddy's private server (phase 0)

About an hour, once. At the end Buddy runs on your own server in the US (East),
reachable only from your devices, with an encrypted database and nightly
encrypted backups.

## Monthly cost

Prices are list prices at the time of writing and approximate; confirm them
when you sign up.

| Item | Choice | Cost per month |
|---|---|---|
| Server | DigitalOcean Basic droplet, 2 GB RAM, 1 vCPU, 50 GB SSD, New York region (close to both the US and South America) | about $12 |
| Private network | Tailscale Personal plan | free |
| Claude API | Pay per use; Buddy stops itself at your monthly budget (default $40) | roughly $10–30 for daily personal use |
| Off-server backup copy (optional) | Backblaze B2 or similar, a few MB | a few cents |
| Domain name, SSL certificate | not needed: Tailscale gives you a private HTTPS address | free |
| **Total** | | **about $22–45** |

Later phases add cost: always-on Listen-in needs a bigger server
(4–8 GB RAM, roughly $24–48 a month) and the WhatsApp tutor needs a separate
number plus Meta's per-message charge.

**Set a hard limit at Anthropic too:** in console.anthropic.com, set a monthly
spend limit slightly above `BUDDY_MONTHLY_BUDGET_USD`. Buddy's own budget check
is the first guard; Anthropic's limit is the backstop.

## What you need before starting

- A laptop (Windows, Mac or Linux) and your Android phone.
- A card for DigitalOcean and Anthropic.

## Step 1 — Claude API key

1. Sign up at console.anthropic.com and add a payment method.
2. Create an API key and keep it somewhere safe (a password manager).
3. Set a monthly spend limit (see above).

## Step 2 — Tailscale, your private network

1. Sign up at tailscale.com (free Personal plan).
2. Install the Tailscale app on your Android phone and your laptop, and log in on both.
3. In the Tailscale admin console, open **DNS** and turn on **MagicDNS** and **HTTPS certificates**.
   Buddy needs HTTPS for the phone's microphone to work.

## Step 3 — Backup key (stays with you, never on the server)

On your laptop, install `age` (github.com/FiloSottile/age) and run:

```
age-keygen -o buddy-backup.key
```

It prints a public key starting with `age1…`. You'll give that to the server.
Keep `buddy-backup.key` in your password manager: it is the only way to open
your backups. The server can write backups but never read them.

## Step 4 — The server

1. Sign up at digitalocean.com.
2. Create a droplet: **Ubuntu 24.04**, region **New York** (closest to both the US and South America), plan **Basic, 2 GB / 1 vCPU**,
   authentication **SSH key** (not password).
3. Don't enable DigitalOcean's own backups; Buddy makes its own encrypted ones.

## Step 5 — Let the server download the code

The repo is private, so give the server a read-only key:

```
ssh root@<droplet-ip>
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

In GitHub, open the `buddy` repo → Settings → Deploy keys → Add deploy key,
paste the key, and leave **write access off**.

## Step 6 — Run the setup script

Still on the server:

```
git clone git@github.com:<you>/buddy.git /tmp/buddy-setup
sudo bash /tmp/buddy-setup/deploy/bootstrap.sh git@github.com:<you>/buddy.git <your age1… public key>
```

The script prints a Tailscale login link: open it and approve the server.
It then closes every public port (public SSH included). From now on, connect with:

```
tailscale ssh root@<server-name>
```

If you ever lock yourself out, DigitalOcean's web console still works.

## Step 7 — Your details and Claude key

```
sudo nano /etc/buddy/buddy.env
```

Set `ANTHROPIC_API_KEY`, `BUDDY_USER_NAME` and, if needed, `BUDDY_MONTHLY_BUDGET_USD`.
`BUDDY_TIMEZONE` is only a fallback: Buddy follows your phone's time zone as you travel.
Leave `BUDDY_DB_KEY` and `BUDDY_TOKEN` as generated. Then:

```
sudo systemctl restart buddy
sudo journalctl -u buddy -f      # watch it start; Ctrl+C to stop watching
```

## Step 8 — Open Buddy on your phone

With Tailscale on, open the address the script printed in Chrome:

```
https://<server-name>.<tailnet>.ts.net/?token=<BUDDY_TOKEN>
```

Allow the microphone, connect your earbuds, tap the orb. Use Chrome's menu →
**Add to Home screen** for an app icon. The token is remembered after the first visit.

## Checking on Buddy

From any device on your tailnet (replace TOKEN with `BUDDY_TOKEN`):

| What | Command |
|---|---|
| Agents and their status | `curl -H "Authorization: Bearer TOKEN" https://<server>/admin/agents` |
| Switch an agent off (kill switch) | `curl -X POST -H "Authorization: Bearer TOKEN" https://<server>/admin/agents/<name>/disable` |
| Switch it back on | `…/admin/agents/<name>/enable`, then `sudo systemctl restart buddy` |
| What agents did | `curl -H "Authorization: Bearer TOKEN" https://<server>/admin/audit` |
| Claude spend this month | `curl -H "Authorization: Bearer TOKEN" https://<server>/admin/usage` |

You can also just say "Buddy, pause the worldclock agent".

## Updating Buddy

```
tailscale ssh root@<server-name>
cd /opt/buddy && sudo -u buddy git pull && sudo systemctl restart buddy
```

## Restoring a backup

Backups are in `/var/backups/buddy/` (last 14 nights). To restore one, copy it
to your laptop and decrypt it with your key:

```
age -d -i buddy-backup.key buddy-YYYYMMDD-HHMM.db.age > buddy.db
```

The result is still encrypted with `BUDDY_DB_KEY`; copy it back to
`/var/lib/buddy/buddy.db` on the server (owner `buddy`) and restart Buddy.
For off-server copies, install `rclone`, configure a remote, and set
`BUDDY_BACKUP_REMOTE` (for example `b2:my-bucket/buddy`) in `/etc/buddy/buddy.env`.

## What protects your privacy

| Layer | Protection |
|---|---|
| Network | No public ports. Only your Tailscale devices can reach the server. |
| Transport | Phone ↔ server is encrypted twice: Tailscale (WireGuard) and HTTPS. |
| Database | Encrypted with SQLCipher; the file on disk is unreadable without `BUDDY_DB_KEY`. |
| Backups | Encrypted again with your `age` key, which never touches the server. |
| Claude | Card, account and routing numbers; US SSN/ITIN; South American IDs (CPF, CNPJ, RUT, CUIT, DNI, cédula); Aadhaar and PAN; passports; one-time codes, CVVs and passwords (English, Spanish, Portuguese) are masked before any text leaves the server. |
| Secrets | Kept in `/etc/buddy/buddy.env`, readable only by root and the Buddy service. |
| Service | Runs as an unprivileged user that can write only its own data and backup folders. |
| Agents | Least access, approvals, daily call limits, audit log, kill switch. |
| Updates | Security updates install automatically. |

The limit: the hosting company controls the hardware. While Buddy is running,
the database key is in the server's memory, so someone with full control of the
machine could in principle read it. Encryption protects against stolen disks,
snapshots and backups, not against the host itself.
