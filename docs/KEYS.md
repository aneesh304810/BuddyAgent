# Every key and connection, step by step (all from your phone)

**One rule for every key:** paste it only into the hidden prompt on your server, never into
a chat, email or message. If a key ever leaks, delete it where you made it and make a new one.

**How you save any key** (in Termux):
```
ssh root@buddy.<your-tailnet>.ts.net
sudo bash /opt/buddy/deploy/set.sh NAME_OF_THE_SETTING
```
Paste the key at the hidden prompt and press Enter. Buddy restarts by itself.
See what's set (names only): `sudo bash /opt/buddy/deploy/set.sh`. Get updates first:
`sudo bash /opt/buddy/deploy/update.sh`.

| # | What | Unlocks | Cost | Time |
|---|---|---|---|---|
| 1 | Email app passwords | Email manager, Gmail agent, money from bank emails | Free | 3 min per account |
| 2 | Serper | Google results, News, Maps places, review checks | Free credits, then about $1 per 1,000 searches | 2 min |
| 3 | Reddit | Real people's opinions and review checks | Free | 3 min |
| 4 | Zapier | Notion, Todoist, Sheets, Slack and thousands more | Free plan: 100 tasks a month | 10 min |
| 5 | SimpleFIN | Every US bank and card transaction and balance | About $15 a year | 10 min |
| 6 | DigitalOcean token | Your real server bill in the cost report | Free | 2 min |
| 7 | Calendar link | Google or Outlook calendar | Free | 2 min |
| 8 | Whisper | Speech recognition on your own server | Free | 5 min |
| 9 | Search level | How freely Buddy searches | — | 1 min |

---

## 1. Email accounts (Gmail; Yahoo and iCloud read-only)
1. Chrome → **myaccount.google.com** → tap your photo → pick the account.
2. **Security** → make sure **2-Step Verification** is on.
3. Search **App passwords** at the top → name it `Buddy` → **Create** → copy the 16 letters.
4. On the server run `sudo bash /opt/buddy/deploy/email.sh`, then type the address and paste the
   password. Repeat for each account (press Enter on an empty address to finish). It checks the
   login before saving.

Check: *"Which email accounts do you have?"*

## 2. Serper (Google results, News, Maps places, reviews)
1. Chrome → **serper.dev** → **Sign up** (Google sign-in works).
2. Open the **Dashboard** or **API Key** page → copy the key.
3. `sudo bash /opt/buddy/deploy/set.sh BUDDY_SERPER_KEY` → paste.

Check: *"Google the best ceviche in Lima"*, *"Find a spa near me"*.
Top up on the same site when the free credits run out; Buddy falls back to web search meanwhile.

## 3. Reddit
1. Chrome → **reddit.com** → log in → open **reddit.com/prefs/apps**
   (if it shows the mobile site, tap ⋮ → **Desktop site**).
2. At the bottom tap **create another app** (or **are you a developer? create an app**).
3. Fill in: name `Buddy`, choose **script**, redirect URI `http://localhost:8080` → **create app**.
4. Copy two things:
   - the **client ID**: the short code just under "personal use script" and the app's name
   - the **secret**: the longer code next to "secret"
5. On the server:
   ```
   sudo bash /opt/buddy/deploy/set.sh BUDDY_REDDIT_CLIENT_ID
   sudo bash /opt/buddy/deploy/set.sh BUDDY_REDDIT_SECRET
   ```

Check: *"What does Reddit say about Cusco in December?"*

## 4. Zapier
1. Chrome → **mcp.zapier.com** → sign in (or create a free Zapier account).
2. Create a new MCP server, name it `Buddy` (if it asks which client, pick "Other" or any).
3. **Add tools**: choose apps and actions you'll really use, for example *Todoist: Create Task*,
   *Google Sheets: Create Spreadsheet Row*, *Notion: Create Page*, *Slack: Send Message*.
   Connect each app's account when asked.
4. Open the server's **Connect** tab → copy the **server URL**. If an **API key** is shown, copy it too.
5. On the server:
   ```
   sudo bash /opt/buddy/deploy/set.sh ZAPIER_MCP_URL
   sudo bash /opt/buddy/deploy/set.sh ZAPIER_MCP_TOKEN      # only if Zapier showed an API key
   ```

Check: *"Which agents do you have?"* (lists zapier), then *"Add a Todoist task: call the dentist
Friday"*. Buddy reads it back and waits for your yes. Keep bank, Gmail and calendars off Zapier.
Details: `docs/ZAPIER.md`.

## 5. SimpleFIN (bank feed, US banks and cards)
1. Chrome → **bridge.simplefin.org** → create an account → subscribe.
2. **Connect a bank** → log in to each bank and card (on SimpleFIN's site; Buddy never sees it).
3. Create a new **app connection** named `Buddy` → copy the **setup token**.
4. `sudo bash /opt/buddy/deploy/bank.sh` → paste the token (it works once).

Check: *"What are my account balances?"* Details: `docs/MONEY.md`.

## 6. DigitalOcean token (cost report)
1. Chrome → **cloud.digitalocean.com** → **API** → **Tokens** → **Generate New Token**.
2. Name `Buddy costs`; choose **Custom scopes** → tick only **billing: read**
   (no custom scopes? choose **Read** only) → generate → copy (shown once).
3. `sudo bash /opt/buddy/deploy/set.sh BUDDY_DO_TOKEN`

Optional extras for the cost report:
```
sudo bash /opt/buddy/deploy/set.sh BUDDY_FIXED_COSTS        # e.g. SimpleFIN=1.25, Domain=1.25
sudo bash /opt/buddy/deploy/set.sh BUDDY_ZAPIER_PLAN_USD    # 0 on the free plan
sudo bash /opt/buddy/deploy/set.sh BUDDY_ALLIN_BUDGET_USD   # e.g. 60
```
Check: *"What do you cost to run?"* or tap **Costs** on the Buddy page.

## 7. Calendar link (optional; the app's Share calendar also works)
1. Chrome → **calendar.google.com** → ⋮ → **Desktop site** → ⚙ **Settings** → your calendar
   → **Integrate calendar** → copy **Secret address in iCal format**.
2. `sudo bash /opt/buddy/deploy/set.sh BUDDY_CALENDAR_ICS_URLS`

## 8. Whisper (speech recognition on your server)
1. `sudo bash /opt/buddy/deploy/whisper.sh`
2. App → **Settings → Speech recognition → Your server (Whisper)**.

## 9. Search level
`sudo bash /opt/buddy/deploy/set.sh BUDDY_SEARCH_LEVEL` → type `thrifty`, `generous` or `max`.

## The app's own switches (Buddy app → Settings)
- **Share my location**: for "near me".
- **This phone → Notification access**: Teams, WhatsApp, bank alerts, Outlook mail.
- **Share calendar**: phone and work calendars (busy times only for work).
- **When earbuds connect**: Listen-in starts by itself; say "Hey Buddy".

## After setting up
```
sudo bash /opt/buddy/deploy/audit.sh        # checks packages and that the settings file is 600 root:root
journalctl -u buddy -n 30 --no-pager        # if something doesn't answer
```
Then ask Buddy: *"Which apps are connected?"* and *"Which agents do you have?"*

## Removing access
Delete the key where you made it (Google app password, Reddit app, Serper key, Zapier MCP server,
SimpleFIN app connection, DigitalOcean token), or clear it on the server:
`sudo bash /opt/buddy/deploy/set.sh NAME` and press Enter on an empty value.
