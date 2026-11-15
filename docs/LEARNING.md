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

## Life memory from your email

Buddy reads the email that is about your own life and remembers what it says, so it knows the people, the
places and the dates without being told twice.

- **What it reads.** Email that looks like real life, not marketing: an appointment or a patient-portal
  notice, your child's school and daycare, your building (a delivery at the front desk, a work order), travel
  documents (a visa or travel authorisation, with its reference number), insurance, payments you made, your
  car, taxes, official letters, invitations, your job, and mail from people you've told Buddy about.
  Newsletters and promotions are skipped.
- **What it keeps.** One short entry per email: what it is, the date and time, who it concerns, the
  organisation, a reference number, and one sentence worth remembering. Ask "when is my doctor's visit?",
  "what did the school send this week?", "what's the reference on Ishaan's Kenya eTA?".
- **Your circle.** Buddy notices who keeps appearing and guesses what they are to you, but a guess is only a
  guess: it asks once ("Neha Sareen keeps coming up... who is she to you?") and treats nothing as fact until
  you say it. Say "Neha Sareen is my wife" or "Nine on the Hudson is my building" any time; their mail is then
  always read and remembered.
- **What it does with it.** An appointment found in email is said once and gets reminders (the evening before,
  and two hours before when the time is known). A delivery waiting at your building is said once. Anything
  with an end date (a visa, a policy, a registration, a warranty, a return window) gets a reminder ahead of
  it. The week's dates appear in the morning briefing. Buddy checks this memory when planning a trip, a school
  day or anything involving the family.
- **Catching up.** "Catch up on my email from the last 30 days" reads older mail for what was missed.
- **Private.** Health email is kept to who, when and where: never results, diagnoses or medicines. Only Claude
  reads this mail, never a free model; it stays in the encrypted database, 400 days after its date. "What do
  you remember from my email?" shows it; "forget ..." deletes it; "ignore ..." stops Buddy keeping someone.

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
