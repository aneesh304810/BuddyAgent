# Learning with Buddy

## The morning English lesson (about five minutes)

Every morning at 8 (say "English lesson at 7" to move it, "turn off the English lesson" to stop it), from your
own sentences to Buddy in the last two days and the words the speech engine struggled to hear:

- **From what you said**: three corrections: what you said, the better version, and why.
- **Grammar**: the one rule those corrections point to, with examples.
- **Say these aloud**: three words to practise, each with a simple respelling (the stressed part in capitals)
  and a tip on the sound to get right.
- **A more natural phrase**: one phrase you use often and a better way to say it.
- **Practice**: one sentence to say to Buddy.

It arrives as a short spoken line with the full lesson on screen; tap **Listen** to hear it. It is kept in
Reports. Your sentences are personal, so only Claude reads them, never a free model. Typing slips and
speech-recognition errors are not counted as mistakes. With fewer than five sentences in two days, or no model
available, you get a general grammar tip from a built-in list and the words the speech engine misheard. The
evening review ("how was my English today?") and live tips ("turn on English tips") stay as they were.

## The morning investing lesson (about ten minutes)

See docs/MONEY.md. Ask "give me today's investing lesson" any time.

## A briefing that learns what you care about

The morning briefing now also carries:

- **News on your interests**: the latest digest for each subject you follow on the News page.
- **Finance**: last night's market headlines, the symbols getting unusual attention, and the latest headline
  for each stock symbol you keep asking about.
- **New events near you**: events for your interests in your city that weren't shown before (Ticketmaster).

It follows what you talk about. Each morning Buddy counts, in your own sentences to it over the last 14 days,
the stock symbols and the subjects you brought up (from a list of about thirty everyday subjects: sports, films,
music, AI, property, travel deals and so on):

- A symbol you ask about on two or more days gets its own headlines in the finance part.
- A subject you raise on three or more days that isn't followed yet is added to your news: one new subject a
  week, three at most. The briefing tells you when it adds one.
- A subject Buddy added that you haven't mentioned for 30 days is dropped, and the briefing says so. Subjects
  you added yourself are never touched.
- The part you talk about most (news, finance or events) comes first.

The counting is done in code on your server; nothing is sent out for it. For a subject outside the list, say
"follow <subject>". Other people's words in Listen-in are never counted.

## The email organizer

"Organize my email" keeps your inbox for real email. Marketing, newsletters and app notifications move into
their own folders (Buddy Promotions, Buddy Newsletters, Buddy Notifications; in Gmail they appear as labels).

- **What always stays in the inbox**: anything that isn't bulk mail (no unsubscribe header), which means every
  email from a person, whatever its subject says; bulk mail whose subject says it matters (security alerts,
  sign-in codes, bills, payments, statements, receipts, orders, deliveries, bookings, tickets, travel changes,
  appointments, taxes); and any sender you told Buddy to keep.
- **What moves**: other bulk mail. Sales and offers go to Promotions, newsletters and digests to Newsletters,
  everything else automated (social networks, app activity, build notices) to Notifications.
- **How it decides**: from the headers only (sender, subject, the bulk-mail markers), in code. The body is
  never read and no AI is involved, so it costs nothing.
- **Preview first**: Buddy always shows what would move (counts, the top senders, examples) and moves mail
  only after your yes. Nothing is ever deleted or marked read.
- **Undo**: "undo the email organizing" puts the last run back in the inbox.
- **Teach it**: "keep emails from github.com", "always put oldnavy.com in promotions", "forget what I said
  about X". A taught sender wins over the built-in rules.
- **Automatic**: after you've organized once by hand, "organize my email automatically" does the last two
  days' mail each night, after the deals reader has taken what it needs from the marketing mail.

It works on every email account Buddy can read (Gmail, Yahoo, iCloud and others). When in doubt a message stays
in the inbox: it is better to leave a promotion than to hide something real.
