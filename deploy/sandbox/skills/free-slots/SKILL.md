---
name: free-slots
description: "Calendar events as JSON -> free time slots over the next days within your waking hours, shown in your time zone and in other people's (a call with family in India, a colleague in Lima), plus double bookings."
tags: [calendar, free, slots, availability, schedule, timezone, meeting, call, conflicts]
data: true
run: "python /buddy/skills/free-slots/free_slots.py {input} {args}"
---
# free-slots

    python /buddy/skills/free-slots/free_slots.py events.json --tz America/New_York [--also Asia/Kolkata]
        [--days 7] [--from 08:00 --to 21:00] [--their-from 08:00 --their-to 22:00] [--min 30]

Input: a JSON list of {title, start, end} (ISO times; UTC if no offset). Free slots are inside
your hours and, with --also, inside their waking hours too. Prints JSON: slots (your time and
theirs, minutes long), conflicts (overlapping events) and the busiest day.
