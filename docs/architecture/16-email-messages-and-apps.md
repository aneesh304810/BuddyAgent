# Email, messages and apps

Gmail and other accounts sorted and summarised, marketing and notices moved out of the inbox, messages, and package tracking.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_important_email["important_email"]
        t_add_email_rule["add_email_rule"]
        t_email_rules["email_rules"]
        t_email_organizer["email_organizer"]
        t_life_memory["life_memory"]
        t_messages["messages"]
        t_packages["packages"]
    end
    subgraph Modules["Modules"]
        mailbrain_py["mailbrain.py<br/>reads new mail from every account, sorts it by priority against your…"]
        mailorganizer_py["mailorganizer.py<br/>marketing, newsletters and app notifications are moved out of the…"]
        lifemail_py["lifemail.py<br/>Buddy reads the email that is about the owner's own life and…"]
        mailfiles_py["mailfiles.py<br/>the PDFs, pictures and documents attached to the owner's life email…"]
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
    you --> t_email_organizer
    t_email_organizer --> mailorganizer_py
    you --> t_life_memory
    t_life_memory --> lifemail_py
    you --> t_messages
    t_messages --> messaging_py
    you --> t_packages
    t_packages --> packages_py
    mailbrain_py --> email_accounts_py
    mailorganizer_py --> email_accounts_py
    lifemail_py --> mailfiles_py
    mailfiles_py --> email_accounts_py
    db[("Your database<br/>mail_circle, mail_files, mail_life, mail_moves, mail_routine, mail_rules, mail_triage, msg_autoreply and more")]
    mailbrain_py --> db
    mailorganizer_py --> db
    lifemail_py --> db
    mailfiles_py --> db
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
    o_4["Shopping and deals"]
    mailbrain_py -. uses .-> o_4
    o_5["Travel and getting around"]
    mailbrain_py -. uses .-> o_5
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `important_email` | What's important in the user's email lately, across all accounts, already sorted by priority (urgent first) with a one-line reason, and anything Buddy organised | - | mailbrain.py |
| `add_email_rule` | Add one of the user's email rules, in their words | - | mailbrain.py |
| `email_rules` | List the user's email rules (with ids), or remove one with remove_id. | - | mailbrain.py |
| `email_organizer` | Keeps the inbox for real email by moving marketing, newsletters and app notifications into their own folders (Buddy Promotions, Buddy Newsletters, Buddy Notific | preview, organize, undo, keep, always, forget, auto, status, archive_preview, archive, archive_auto | mailorganizer.py |
| `life_memory` | What Buddy remembers about the owner's life from their own email: family and the people around them, children's school and daycare, health appointments (logisti | show, teach, ignore, forget, catch_up, attachments, trips, providers, routines | lifemail.py |
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

### mailorganizer.py

```text
The email organizer: marketing, newsletters and app notifications are moved out of the inbox into their own
folders, so what is left in the inbox is real email: from people, and the automated mail that matters.

How a message is judged (from its headers only: the body is never read, and no AI is involved):
  stays in the inbox, always
    - anything that is not bulk mail: no unsubscribe header and not marked bulk. A person's email has neither,
      so a personal email can never be moved, whatever its subject says
    - bulk mail that matters, by its subject: security and sign-in alerts, codes, bills, payments, statements,
      receipts, orders, deliveries, bookings, tickets, travel changes, appointments, taxes, job offers
    - any sender the owner said to keep
  is moved, when it is bulk mail and none of the above
    Buddy Promotions      sales, offers, coupons, deals
    Buddy Newsletters     newsletters, digests, roundups, blog and product updates
    Buddy Notifications   everything else automated: social networks, app activity, build and system notices

Nothing is ever deleted and nothing is marked read: a moved message sits in its folder exactly as it arrived.
Every move is written down, so "undo" puts a run's messages back in the inbox. Moving a message back yourself
(or saying "keep emails from X") teaches Buddy to leave that sender alone.

The owner is always shown a preview first (how many would move, where, with examples by sender), and Buddy moves
mail only after their yes. With "organize my email automatically" on, new mail is organized each night after the
deals reader has taken what it needs from the marketing mail. Works with any account Buddy can read over IMAP
(Gmail, Yahoo, iCloud and others); in Gmail the folders appear as labels and the mail leaves the inbox.

Old promotions are archived, when the owner asks: a sale's email is of no use once the sale is over. Promotions
(judged as above) older than a number of days, 10 unless the owner names another, are moved from the inbox and from
Buddy Promotions to the account's own archive: Gmail's All Mail, or the folder the mail service marks as its
archive. It is the move a mail app's Archive button makes. Nothing is deleted; a starred message stays; newsletters
and notifications are left alone. The same rules hold: a preview first, the owner's yes, every move written down
so "undo" puts a run back in the inbox, and it runs each night only once the owner switches that on. An account
where no archive can be found is left as it is.
```

- Tables: `mail_moves`
- Settings: `BUDDY_EMAIL_ADDRESS`, `BUDDY_EMAIL_APP_PASSWORD`
- Uses: dealmail.py, email_accounts.py, memory.py, tools.py

### lifemail.py

```text
Life memory from email: Buddy reads the email that is about the owner's own life and remembers what it says,
so it knows the people, the places and the dates without being told twice.

  What is read    new email that looks like real life, not marketing: a sender already in the owner's circle, or
                  words that mark an appointment, a child's school or daycare, the home or building, a travel
                  document, an insurer, a payment that was made, or a real person writing. Mail the email
                  manager sorted as low (newsletters, promotions) is skipped unless it is from the circle.
  What is kept    for each such email, at most one dated entry (mail_life): its category (health, school,
                  childcare, home, travel, money, insurance, family, work), what it is (appointment, document,
                  notice, payment, update), the date and time, who it concerns, the organisation, a reference
                  number when there is one, and one plain sentence worth remembering. The sentence is also kept
                  as a searchable note in the brain, so "what's the reference on Ishaan's Kenya eTA?" or "when
                  is my doctor's visit?" can be answered later.
  The circle      the people and organisations that keep appearing (mail_circle): a sender, a name, and what
                  they are to the owner. Buddy only guesses a relation ("travels with you; a school writes to
                  you both: family?") and asks once; nothing is treated as fact until the owner confirms or
                  says it ("Neha Sareen is my wife"). Confirmed people go into the brain as people.
  What it does    an appointment found in email is said once and gets reminders (the evening before, and two
                  hours before when the time is known); a delivery waiting at the building is said once; the
                  rest is kept quietly and shows in the morning briefing when it is dated within the week. Anything
                  with an end date (a visa, a policy, a registration, a warranty, a return window) gets a
                  reminder ahead of it.

Health email is kept to logistics only: who, when and where. Never results, diagnoses or medicines, and a
patient-portal message is only "there is a new message", since its content isn't in the email anyway.
All of it is personal: it is read by Claude only, never by a free model, and stays in the encrypted database.
The owner can see it, correct it and delete it (the life_memory tool). Kept 400 days after its date.
```

- Tables: `mail_circle`, `mail_life`, `mail_routine`
- Uses: brain.py, config.py, mailfiles.py, memory.py, money.py, thinking.py, tools.py

### mailfiles.py

```text
Email attachments, read: the PDFs, pictures and documents attached to the owner's life email are opened,
understood and remembered, instead of only their file names being seen.

  Which ones      attachments on email that life memory kept (lifemail.py): a travel authorisation, a school's
                  weekly letter, an insurer's receipt, an invitation that is only a picture. Never attachments
                  on marketing mail. PDFs, pictures, Word and Excel files and text, up to 10 MB each and four
                  per email. A picture inside the email body counts only when the email has almost no text
                  (an invitation sent as an image), not logos and signatures.
  How             the email is fetched again from the owner's own mailbox, read-only, by its Message-ID; each
                  attachment goes into the library (library.py), which reads it (Claude reads scans and
                  pictures), summarises it, indexes it for search and keeps the original encrypted.
  Private ones    documents from travel, official, health, tax, insurance and money email go into the vault:
                  their summary is known, the full text opens only after the owner's yes.

Read by Claude only, kept in the encrypted library. The owner can open or delete each one there.
```

- Tables: `mail_files`
- Uses: email_accounts.py, library.py, memory.py

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

- Relies on: Claude: the brain; Memory; Money; Phone calls; Shopping and deals; Travel and getting around
- Relied on by: Buddy Connect; Claude: the brain; Memory; Money; Shopping and deals; Speaking up on its own: briefing, reminders and alerts; The server and the app

