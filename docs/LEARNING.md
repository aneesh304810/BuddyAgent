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
