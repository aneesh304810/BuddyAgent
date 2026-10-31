---
name: deadlines
description: "Emails, messages or notes -> every deadline and action date in them ('due Nov 6', 'by Friday', 'check in before 10/30'), resolved to real dates from when each was sent, soonest first."
tags: [deadlines, dates, due, action, todo, email, calendar, reminders]
data: true
run: "python /buddy/skills/deadlines/deadlines.py {input} {args}"
---
# deadlines

    python /buddy/skills/deadlines/deadlines.py emails.json [--days 60] [--dmy]

Input: a JSON list of {date, from, subject, text}, or plain text (then dates count from today).
A date counts when words like due, by, before, deadline, expires, until, check in, renew, pay,
appointment are near it. `--dmy` reads 03/04 as 3 April (default US: March 4). Prints JSON:
upcoming deadlines (date, days left, what, from, subject) and overdue ones.
