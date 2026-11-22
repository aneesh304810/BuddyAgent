# Travel companion on glasses: the use cases, organised for Buddy

Written on 22 November 2026 from a list of 12 use cases and 5 scenarios the owner brought. **This page is a plan.
Nothing in it runs on glasses yet.** "Works on the phone" means the code exists and is tested; it does not mean it
was checked on a trip.

## 1. How it is organised

- **One app, not a new layer.** The travel companion is an app on the glasses framework (`docs/BUDDY-XR.md`). It
  uses the Travel and Going out modules Buddy already has, through the link between glasses and server
  (`docs/BWCP.md`). There is no separate "travel agent" service.
- **Four groups, by what a use case needs.** Tell, See, Hear, and Left out (section 2). The groups are also the
  build order.
- **Speech or lens is not a choice to make.** An app says what the wearer should get; the device's profile decides
  whether it is spoken or shown (`docs/PLATFORM.md`, rule 3). The same app serves audio-only and display glasses.
- **The three rules stay on.** Privacy before anything leaves the device or reaches a model; nothing is booked,
  paid, changed or sent without a yes; capabilities, never brands.

## 2. The four groups

| Group | What it needs | Module (`docs/PLATFORM.md`) | State |
|---|---|---|---|
| **Tell**: things Buddy already knows | The glasses carry Buddy's own alerts and answers; the wearer's voice reaches Buddy | Devices, Link, Agent | The knowledge works on the phone. The link is built. Alerts are not sent to glasses, and the wearer's words do not reach Buddy yet |
| **See**: needs the camera | A photo from the glasses, understood | Vision | Not built |
| **Hear**: needs live audio on the glasses | Speech in and out on the device, as it happens | Voice on a device | Not built on glasses. The phone can hear the room and translate what it heard |
| **Left out** | A source of data that does not exist openly | none | Not planned: Buddy would be guessing |

## 3. The 12 use cases

| # | Use case | Group | What Buddy has today | Missing |
|---|---|---|---|---|
| 1 | Flight companion: status, gate changes, boarding, delays, connections | Tell | Flight tracking: checked every 15 minutes from 4 hours before departure; messages on delays, gate and terminal changes, cancellation, diversion, take-off, landing, bag carousel. Trip alerts: the evening before, the flight day, time to leave, at the airport, landed | Sending these to the glasses. "A 7-minute walk to the gate" is left out: no source for walking times inside a terminal |
| 2 | Visual airport assistant: read signs, find the gate, baggage claim, lounges | See | Nothing | Vision. Buddy could read a sign you look at. It could not guide you through a terminal (Left out) |
| 3 | Travel guide: recognise a landmark, explain it, recommend attractions | See, Tell | Places search with ratings and reviews; Claude can explain a place you name | Recognising what you look at needs Vision |
| 4 | Hotel: reservation details, check-in reminders, room and amenities, alternatives | Tell | Check-in day alert; hotel search with the true total and the catches; direct price against booking sites; book-or-wait call. Reservation details come from your own emails | To glasses. Room and amenity details are only what your confirmation says |
| 5 | Live translation: conversations, signs, menus | Hear, See | The phone can translate what it heard in the room in the last 10 minutes. Phone calls work in several languages | Live two-way translation on glasses (Hear). Signs and menus (See) |
| 6 | Restaurants: nearby, ratings and prices, reservations, menus | Tell, See | Restaurant search (rating, price, open now, whether they take reservations), booking links, and a reservation phone call with your yes | To glasses. Reading a menu needs Vision |
| 7 | Baggage: claim information, tracking, lost luggage | Tell, Left out | Bag carousel from flight status. Buddy can phone the airline with your yes and wait on hold | Bag tracking has no open source (Left out). Buddy cannot file a claim itself |
| 8 | Navigation: walking and transit directions | Tell (part) | Train and subway alerts and next trains for your lines; time to leave for calendar events; opens a ride app for you to confirm | Turn-by-turn directions are not built, on the phone or on glasses |
| 9 | Travel shopping: compare prices, local deals, tax-free | Tell, See | "Is this a good price?" by name, from the price book, with a buy-or-wait call | Recognising the product in front of you needs Vision. Tax-free rules are left out: no source |
| 10 | Lounge and airport services | Tell | Lounge access is known from your cards' benefits. Places search for amenities | To glasses. Which lounge is in which terminal is only as good as the places search |
| 11 | Personal recommendations: weather, budget, family, free time | Tell | Places search with your taste, weather, your calendar's free time | To glasses |
| 12 | Business travel: meetings, getting there, flights and calendar, expenses | Tell, See | Time to leave for a meeting; flights and calendar together; card transactions from your banks | To glasses. A photo of a receipt needs Vision |

## 4. The five scenarios

| Scenario | Group | State |
|---|---|---|
| A. Gate change, spoken as it happens | Tell | The alert exists on the phone. To glasses: not built. No walking time |
| B. A hotel or fare drops below your target | Tell | Saved searches are re-run daily and book-or-wait calls are followed. To glasses: not built. Buddy compares; it never rebooks by itself |
| C. Look at a sign and ask | See | Not built |
| D. "A route with better lighting and more public activity" | Left out | No source of such data. Buddy will not claim a route is safer |
| E. Calendar event coming, leave now | Tell | Exists on the phone. To glasses: not built |

## 5. Build order (proposed; each step waits for the owner's go)

This is step 3 of the platform's build order: one device, one app, end to end on real glasses.

1. **Tell.**
   - Buddy's own alerts reach the glasses through the link, each under the privacy rule (a personal line is shown
     on a private lens and is not spoken aloud).
   - The wearer's voice reaches Buddy. The phone's microphone first (Talk mode exists), the glasses' own after.
   - Covers use cases 1, 4, 6, 10, 11, 12, the Tell parts of 7, 8 and 9, and scenarios A, B, E.
2. **See.** A photo taken on the wearer's request goes to Claude only, and the answer is spoken or shown. Covers
   2, 3, the See parts of 5, 6, 9, 12, and scenario C.
3. **Hear.** Live translation of a conversation. Covers the rest of 5.

Not checked anywhere real: nothing has run on real glasses, and step 1 needs the device.

## 6. Left out, and why

- Walking routes and times inside airports: no open indoor maps Buddy may use.
- Bag tracking: airlines offer no open service for it.
- "Safer route" advice: there is no reliable data behind it.
- Tax-free shopping rules: they differ by country and shop, with no dependable source.

Each can come back when a real source exists.

## 7. What differs from the list as it came

- **Amadeus.** The list builds on Amadeus. Its self-service programme closed in July 2026; access is by enterprise
  contract. Buddy already has flight and hotel search (Duffel, Google Flights and Hotels, Travelpayouts) and
  flight status (AirLabs, FlightAware, the airline's own messages).
- **Names.** "MasterOS" and "Central AI Orchestra" are not Buddy's names. Maestro is the gateway for AI models;
  the rest is the server (`docs/PLATFORM.md`, decision 3).
- **Hardware.** The list names four pairs of glasses. Buddy has adapters written for three devices, none run on
  glasses yet, and version 1 starts with one.
- **Scope.** The list proposes six features for a first release. This page proposes the Tell group alone first.

## 8. Open decisions

- Whether the travel companion is the first app on real glasses, ahead of the restaurant one.
- Which device is first.
- Whether turn-by-turn directions are wanted at all, and from which maps service.
