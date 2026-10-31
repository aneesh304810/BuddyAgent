---
name: itinerary-check
description: "Bookings (flights, trains, hotels) as JSON -> one timeline in local times, with problems flagged: tight or missed connections, long layovers, nights without a hotel, overlapping bookings, overnight travel and early starts."
tags: [travel, itinerary, flights, hotels, trip, connection, layover, timezone, booking]
data: true
run: "python /buddy/skills/itinerary-check/itinerary.py {input} {args}"
---
# itinerary-check

    python /buddy/skills/itinerary-check/itinerary.py bookings.json

Input: a JSON list. Flights / trains: {"type": "flight", "name": "LA2471", "from": "MIA", "to": "LIM",
"depart": "2026-11-03T08:10", "depart_tz": "America/New_York", "arrive": "2026-11-03T13:40",
"arrive_tz": "America/Lima", "international": true}. Hotels: {"type": "hotel", "name": "...",
"city": "Lima", "check_in": "2026-11-03", "check_out": "2026-11-06"}. Times with an offset
(+05:30) need no _tz. Prints JSON: the timeline (local times), and problems with a plain reason.
