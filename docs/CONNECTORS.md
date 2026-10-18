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
1. Google Calendar on a computer → ⚙ **Settings** → click your calendar on the left →
   **Integrate calendar** → copy **Secret address in iCal format**.
   (Outlook: Settings → Calendar → Shared calendars → Publish a calendar → ICS link.)
2. On the server, add to `/etc/buddy/buddy.env` (several calendars: comma-separated):
   ```
   BUDDY_CALENDAR_ICS_URLS=https://calendar.google.com/calendar/ical/.../basic.ics
   ```
3. `sudo systemctl restart buddy`

Then try: *"What's on my calendar today?"* The morning briefing now includes your day too.
Treat the secret address like a password; if it ever leaks, reset it in the same Google screen.

### Email (Gmail or any IMAP mailbox): about 5 minutes
1. Turn on **2-Step Verification** for your Google account.
2. Go to **myaccount.google.com → Security → App passwords**, create one named "Buddy",
   and copy the 16-letter password.
3. Add to `/etc/buddy/buddy.env`:
   ```
   BUDDY_EMAIL_ADDRESS=you@gmail.com
   BUDDY_EMAIL_APP_PASSWORD=abcdabcdabcdabcd
   # optional: BUDDY_EMAIL_IMAP_HOST=imap.gmail.com   BUDDY_EMAIL_FOLDER=INBOX
   ```
4. `sudo systemctl restart buddy`

Buddy opens the mailbox **read-only** and never marks anything as read. Try: *"Did my
flight confirmation come?"*, *"Any card bills this week?"*. Your bank's alert emails
become the first source for the money manager.

To remove access at any time: delete the app password in your Google account.

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
| Notion, Slack, Google Drive, others | Their official MCP servers, as agents | Reads; acts with your yes | As needed |

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
