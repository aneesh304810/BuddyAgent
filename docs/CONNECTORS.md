# Connectors: how Buddy plugs into your apps

Buddy connects to apps one systematic way. Each app is a small plug-in with the
same shape, and one hub runs them all:

```
 Your apps ──► Connector (one per app) ──► Hub ──────────────► Encrypted memory
 (Calendar,      • settings it needs        • runs the schedule     (records, masked)
  Gmail, ...)    • how often to sync        • masks private numbers        │
                 • fetch() → records        • de-duplicates                 ▼
                                            • health + kill switch    Buddy's tools:
                                                                      agenda, search_my_apps,
                                                                      app_connections
```

## The rules every connector follows

| Rule | What it means |
|---|---|
| **Read-only** | Connectors only read. Anything that changes an app (send, book, pay, delete) is an *agent* action that waits for your yes. |
| **Least access** | The narrowest access that works: a private calendar link instead of your Google account; an app password and a read-only mailbox instead of your password. |
| **Secrets stay on the server** | Settings live in `/etc/buddy/buddy.env` (readable only by root and Buddy), never in code or chat. |
| **Masked and encrypted** | Card numbers, IDs and codes are masked before storage; records live in the SQLCipher database. |
| **Untrusted content** | Text from emails and apps is information, never instructions. If an email says "pay this now", Buddy tells you and asks. |
| **Short memory** | Calendar keeps the next 30 days; email the last 30 days. Older items are deleted automatically. |
| **Health and kill switch** | Each connector shows when it last synced and any error, and can be switched off. |

## Connect them now

### Calendar (Google, Outlook or iCloud): about 2 minutes
1. On your phone, open **calendar.google.com** in Chrome, tap **⋮ → Desktop site**, then
   ⚙ **Settings** → click your calendar on the left → **Integrate calendar** → copy
   **Secret address in iCal format**. (The Google Calendar app doesn't show it.)
   (Outlook: Settings → Calendar → Shared calendars → Publish a calendar → ICS link.)
2. In Termux, `ssh root@buddy`, then `nano /etc/buddy/buddy.env` and add a line
   (several calendars: comma-separated):
   ```
   BUDDY_CALENDAR_ICS_URLS=https://calendar.google.com/calendar/ical/.../basic.ics
   ```
3. `sudo systemctl restart buddy`

Then try: *"What's on my calendar today?"* The morning briefing now includes your day too.
Treat the secret address like a password; if it ever leaks, reset it in the same Google screen.

### Email (Gmail or any IMAP mailbox): about 5 minutes
1. Turn on **2-Step Verification** for your Google account.
2. In Chrome, go to **myaccount.google.com → Security → App passwords** (or search
   "App passwords" at the top of that page), create one named "Buddy", and copy the
   16-letter password.
3. In Termux, `ssh root@buddy`, `nano /etc/buddy/buddy.env`, and add:
   ```
   BUDDY_EMAIL_ADDRESS=you@gmail.com
   BUDDY_EMAIL_APP_PASSWORD=abcdabcdabcdabcd
   # optional: BUDDY_EMAIL_IMAP_HOST=imap.gmail.com   BUDDY_EMAIL_FOLDER=INBOX
   ```
4. `sudo systemctl restart buddy`

Buddy opens the mailbox **read-only** and never marks anything as read.

The same app password also powers the **Gmail agent**, which can act on your mail:

| You say | Buddy | Asks first? |
|---|---|---|
| "Any emails from Chase this week?" | Searches with Gmail's own search | No |
| "Read me Priya's email" | Reads it (it stays unread) | No |
| "Draft a reply saying Friday works" | Saves a draft in Gmail for you to check | No |
| "Send it" / "Email Raj the address" | Reads back who, subject and gist | **Yes** |
| "Clean up my inbox" | Shows who fills it, proposes a plan, gives counts | **Yes**, before each step |
| Archive, trash, mark read, label | Up to 500 at a time; trash can be undone for 30 days | **Yes** |
| "How do I unsubscribe from Shop?" | Finds the unsubscribe link for you | No |

Nothing is ever deleted for good. It's on by default once the app password is set
(the `gmail` agent in `agents.yaml`; if your server was set up before it existed, copy
that block from `agents.example.yaml`). Try: *"Did my
flight confirmation come?"*, *"Any card bills this week?"*. Your bank's alert emails
become the first source for the money manager.

To remove access at any time: delete the app password in your Google account.

### Phone notifications and calendars (every phone with the Buddy app)
On each Android phone with Buddy (your Fold, a work phone...): **Settings → This phone**.

1. **Name** the phone ("Fold 8", "Work phone"), so you can tell them apart.
2. **Turn on notification access.** Buddy then sees *all* this phone's notifications:
   Teams messages and meeting alerts, WhatsApp, SMS, bank charges, deliveries, gate changes.
   Skipped: Buddy's own, ongoing ones (music, navigation) and system ones.
3. **Share calendar** sends the next 7 days of this phone's calendars every 30 minutes,
   including a **work calendar** that Outlook or Teams syncs to the phone.
   **Work calendars: busy times only** (on by default) sends "Busy 10:00–11:00" without
   titles or places, so confidential meeting details never reach your server.

On the server, choose which apps Buddy reads aloud as they arrive (everything else is
just remembered, so you can ask *"any WhatsApp from Priya?"*), and quiet hours:
```
BUDDY_ANNOUNCE_APPS=Teams, WhatsApp
BUDDY_QUIET_HOURS=22:00-07:00
```
Codes, card numbers and passwords are masked; one-time codes are never read out.
Notifications are kept 7 days. Limits: an app inside an Android **Work profile** (briefcase
icon) keeps its notifications and calendar private from personal apps by design; an iPhone
can't share notifications with apps at all.

### Check and control
| What | How |
|---|---|
| Status | Say *"Which apps are connected?"* or `curl -H "Authorization: Bearer TOKEN" https://<server>/admin/connectors` |
| Sync now | *"Check my email now"* or `POST /admin/connectors/email/sync` |
| Switch off | `POST /admin/connectors/email/disable` (and `/enable` to switch back on) |
| Change schedule | `BUDDY_CONN_EMAIL_EVERY=30` (minutes; same for `CALENDAR`) |

## Every app on the plan

| App | How Buddy connects | Reads / acts | When |
|---|---|---|---|
| Calendar (Google, Outlook, iCloud) | Private iCal link | Reads | ✅ Now |
| All phone notifications (Teams, WhatsApp, SMS, bank...) | Buddy app's notification access, on every Android phone | Reads; reads aloud the apps you choose | ✅ Now |
| Phone calendars, incl. work (Outlook/Teams) | Buddy app reads the phone's calendar; work = busy times only | Reads | ✅ Now |
| Email (Gmail, Outlook, any IMAP) | App password, read-only mailbox | Reads | ✅ Now |
| Bank and card alerts, Amazon, Uber, airline and hotel confirmations | Through the email connector | Reads | ✅ Now (via email) |
| Bank accounts and cards | Actual Budget + SimpleFIN (read-only bank feed; Buddy never has your bank password) | Reads | Phase 1b |
| Airbnb history | Your Airbnb data export, imported as a file | Reads | Phase 1a |
| Google Takeout (Maps timeline, places, photos metadata) | Export file import | Reads | Phase 1a |
| Gmail drafting and sending | Gmail API with your own Google Cloud OAuth app | Acts, with your yes | Phase 3 |
| Google Calendar booking | Calendar API OAuth | Acts, with your yes | Phase 3 |
| Contacts | Google People API, or a vCard export | Reads | Phase 3 |
| Oura Ring 4 | Oura API v2 (OAuth) | Reads | Phase 7 |
| WhatsApp (tutor lessons to you) | WhatsApp Business API | Sends only to you | Phase 5 |
| Booking sites, forms, subscriptions | Browser agent in a sandbox | Acts, with your yes | Phase 6 |
| Teams, Zoom, Meet | Laptop helper or meeting bot | Reads (Call Co-pilot) | Later |
| Notion, Todoist, Google Sheets, Slack and thousands more | Zapier agent (`docs/ZAPIER.md`) | Lookups; changes with your yes | ✅ Now (bring your Zapier link) |
| Notion, Slack, Google Drive, others (direct) | Their official MCP servers, as agents | Reads; acts with your yes | As needed |

## For developers: adding a connector

One class in `backend/buddy/connectors/`, registered in `hub.py`'s `REGISTRY`:

```python
class OuraConnector(Connector):
    name = "oura"
    title = "Oura Ring"
    description = "Sleep, readiness and activity."
    required = {"BUDDY_OURA_TOKEN": "Oura access token"}
    every_minutes = 60
    snapshot = False
    keep_days = 90

    async def fetch(self) -> list[Record]:
        ...  # call the API (asyncio.to_thread for blocking clients), return Records
```

The hub handles scheduling, masking, storage, de-duplication, retention, health and
the tools. Add a test with a fake API, like `tests/test_connectors.py`.
