# Email, messages and apps

Gmail and other accounts sorted and summarised, messages, and package tracking.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_important_email["important_email"]
        t_add_email_rule["add_email_rule"]
        t_email_rules["email_rules"]
        t_messages["messages"]
        t_packages["packages"]
    end
    subgraph Modules["Modules"]
        mailbrain_py["mailbrain.py<br/>reads new mail from every account, sorts it by priority against your…"]
        email_accounts_py["email_accounts.py<br/>All of the user's email accounts, read from the settings file"]
        messaging_py["messaging.py<br/>reply to WhatsApp (and other chat apps) from your own account,…"]
        packages_py["packages.py<br/>from shipping emails and notifications (free, no key), and 17TRACK…"]
    end
    you --> t_important_email
    t_important_email --> mailbrain_py
    you --> t_add_email_rule
    t_add_email_rule --> mailbrain_py
    you --> t_email_rules
    t_email_rules --> mailbrain_py
    you --> t_messages
    t_messages --> messaging_py
    you --> t_packages
    t_packages --> packages_py
    mailbrain_py --> email_accounts_py
    db[("Your database<br/>mail_rules, mail_triage, msg_autoreply, msg_autoreply_log, packages")]
    mailbrain_py --> db
    messaging_py --> db
    packages_py --> db
    o_0["Claude"]
    mailbrain_py -. uses .-> o_0
    o_1["Memory"]
    mailbrain_py -. uses .-> o_1
    o_2["Money"]
    mailbrain_py -. uses .-> o_2
    o_3["Phone calls"]
    mailbrain_py -. uses .-> o_3
    o_4["Travel and getting around"]
    mailbrain_py -. uses .-> o_4
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `important_email` | What's important in the user's email lately, across all accounts, already sorted by priority (urgent first) with a one-line reason, and anything Buddy organised | - | mailbrain.py |
| `add_email_rule` | Add one of the user's email rules, in their words | - | mailbrain.py |
| `email_rules` | List the user's email rules (with ids), or remove one with remove_id. | - | mailbrain.py |
| `messages` | Reply to WhatsApp, Messages, Telegram or Signal from the user's own account, through the phone's notification (free; works for chats with a recent notification) | chats, reply, auto_reply, auto_replies, stop_auto_reply | messaging.py |
| `packages` | The user's packages: from shipping emails and notifications (Amazon, UPS, FedEx, USPS, DHL), and 17TRACK live status when its key is set | list, track, remove, test | packages.py |

## Technical design, module by module

### mailbrain.py

```text
Email manager: reads new mail from every account, sorts it by priority against
your rules, tells you about what matters, and organises mail the way you asked.
The same call also reads charges, bills and payments for the money agent.

  urgent  told straight away (outside quiet hours), e.g. a bill due tomorrow, a fraud
          alert, a flight change, family or your boss needing an answer
  normal  worth reading: in "what's important in my email?" and the morning briefing
  low     newsletters, promotions, routine notifications: never mentioned

Rules are your own words ("anything from Chase about a payment is urgent", "label
Uber and Lyft receipts Travel", "archive LinkedIn notifications"). Rules that change
mail (label, archive, mark read) are only added after your yes, then run by
themselves on new Gmail mail; nothing is ever deleted or sent automatically.

New mail is sorted in small batches by the cheap model, about a tenth of a cent each.
```

- Tables: `mail_rules`, `mail_triage`
- Uses: agent.py, budgets.py, cancelling.py, config.py, email_accounts.py, memory.py, money.py, thrift.py, tools.py, usage.py

### email_accounts.py

```text
All of the user's email accounts, read from the settings file.

Account 1 uses BUDDY_EMAIL_ADDRESS and BUDDY_EMAIL_APP_PASSWORD; accounts 2 to 9
use BUDDY_EMAIL_2_ADDRESS, BUDDY_EMAIL_2_APP_PASSWORD and so on. Servers are
found from the address (Gmail, Yahoo, iCloud...); BUDDY_EMAIL[_N]_IMAP_HOST,
_SMTP_HOST, _SMTP_PORT and _FOLDER override them. Google Workspace addresses
on your own domain work like Gmail, which is also the default for unknown domains.
```

- Settings: `BUDDY_EMAIL`, `BUDDY_EMAIL_`, `BUDDY_EMAIL_2_ADDRESS`, `BUDDY_EMAIL_2_APP_PASSWORD`, `BUDDY_EMAIL_ADDRESS`, `BUDDY_EMAIL_APP_PASSWORD`

### messaging.py

```text
Messages: reply to WhatsApp (and other chat apps) from your own account, through the phone's notification.

How: when a WhatsApp, Messages, Telegram or Signal notification arrives, the Buddy app keeps that chat's "Reply"
action (the same one you use from the notification shade). "Tell Neha I'll be 10 minutes late" -> Buddy finds the
chat, shows the exact text, and sends it only after the user's yes. Free: no WhatsApp Business account, no Meta
fees; it works for chats with a recent notification on the phone.

Auto-replies, only by the user's own rules: for a named contact (or anyone saved in Buddy's people), in a
situation (driving, in a meeting, quiet hours, or always), with the user's own text, signed as Buddy. Never to
groups or unknown numbers, at most once per chat every 2 hours, and the user is told each time. Buddy never makes
up an answer on the user's behalf without a yes: for anything beyond the user's own away-message, it drafts a
reply and asks.

Memories: chat messages feed the brain like emails do (connect.py): promises with dates, plans, and facts about
people, read by Claude's quick model, never a free model.
```

- Tables: `msg_autoreply`, `msg_autoreply_log`
- Uses: contacts.py, memory.py, tools.py

### packages.py

```text
Package tracking: from shipping emails and notifications (free, no key), and 17TRACK for live status.

Free path: shipping emails and phone notifications (Amazon, UPS, FedEx, USPS, DHL, store "your order shipped"
mails) are read on Buddy's server with text rules for tracking numbers and status (shipped, out for delivery,
delivered, delayed, exception). Each package gets the carrier's own tracking link.
17TRACK (optional, BUDDY_17TRACK_KEY, free tier at 17track.net): numbers are registered once and their live status
is checked every two hours while in transit (POST api.17track.net/track/v2.2/register and gettrackinfo, header
17token). 17TRACK's reply format is from its documentation; the first real check confirms it.

Said once each: out for delivery today, delivered (and, when the user is away from home, that it's waiting there),
delayed or a problem. Only tracking numbers go to 17TRACK. Delivered packages are dropped 30 days later.
```

- Tables: `packages`
- Settings: `BUDDY_17TRACK_KEY`
- Uses: memory.py, tools.py, travel.py

## How it connects

- Relies on: Claude: the brain; Memory; Money; Phone calls; Travel and getting around
- Relied on by: Claude: the brain; Memory; Shopping and deals; Speaking up on its own: briefing, reminders and alerts; The server and the app

