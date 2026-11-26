# The server and the app

The one server the phone talks to: the chat connection, the app's pages and their data, and the door where a pair of smart glasses joins Buddy's brain.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_glasses_describe["glasses_describe"]
        t_glasses_show["glasses_show"]
        t_glasses_ask["glasses_ask"]
        t_glasses_choose["glasses_choose"]
    end
    subgraph Modules["Modules"]
        server_py["server.py<br/>HTTP + WebSocket server"]
        glasslink_py["glasslink.py<br/>where a pair of glasses joins Buddy's brain (the Devices and Link…"]
    end
    you --> t_glasses_describe
    t_glasses_describe --> glasslink_py
    you --> t_glasses_show
    t_glasses_show --> glasslink_py
    you --> t_glasses_ask
    t_glasses_ask --> glasslink_py
    you --> t_glasses_choose
    t_glasses_choose --> glasslink_py
    server_py --> glasslink_py
    o_0["Agents and skills"]
    server_py -. uses .-> o_0
    o_1["Buddy Connect"]
    server_py -. uses .-> o_1
    o_2["Buddy Intelligence Platform"]
    server_py -. uses .-> o_2
    o_3["Claude"]
    server_py -. uses .-> o_3
    o_4["Email, messages and apps"]
    server_py -. uses .-> o_4
    o_5["Fixing and explaining itself"]
    server_py -. uses .-> o_5
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `glasses_describe` | The smart glasses connected to Buddy right now, if any: display size or none, speaker, microphone, camera, how the wearer gives input, battery | - | glasslink.py |
| `glasses_show` | Put something in front of the wearer of the connected glasses: a title and a few short lines | - | glasslink.py |
| `glasses_ask` | Ask the wearer of the connected glasses a yes-or-no question and wait for the answer (a tap or the word) | - | glasslink.py |
| `glasses_choose` | Offer the wearer of the connected glasses a short list (2 to 8 options) and wait for their choice | - | glasslink.py |

## Technical design, module by module

### server.py

```text
HTTP + WebSocket server. The phone connects to /ws and streams text both ways.

Protocol (JSON messages):
  phone -> server  {"type": "hello", "timezone": "America/Sao_Paulo"}  on connect
                   {"type": "user", "text": "..."}   something the user said
                   {"type": "interrupt"}              user started talking over Buddy
                   {"type": "ambient", "text": "...", "lang": "hi-IN"}
  phone -> server  {"type": "audio", "pcm": "<base64 16 kHz mono 16-bit>", "lang": "auto", "mode": "talk"}
  server -> phone  {"type": "heard", "text": "...", "lang": "es"}  (Whisper on the server)
  phone -> server  {"type": "rate", "turn": "a1b2c3", "up": true}  thumbs on a reply (null clears)
  phone -> server  {"type": "location", "lat": -12.12, "lng": -77.03, "accuracy": 50}  (when shared)
  server -> phone  {"type": "vocabulary", "words": [...]}  names and words to recognise (after hello)
  server -> phone  {"type": "thinking"}  the question arrived and Buddy is working on it
  server -> phone  {"type": "approval", "question": "..."}  Buddy is waiting for a yes:
                   show Approve / Decline; they send a user message "Yes, go ahead." / "No."
                                                      listen-in mode: speech around the user
                   {"type": "ambient_stop"}           listen-in mode switched off
                   {"type": "notification", "device", "app", "app_name", "title", "text",
                    "posted_at", "key"}               a notification on that phone
                   {"type": "phone_calendar", "device", "events": [{id, title, start,
                    end, calendar, location, all_day, busy_only}]}  the phone's next week
  server -> phone  {"type": "say", "text": "...", "filler": true?, "display": "..."?}  one sentence to speak
                                                      (display: what to show instead, e.g. English)
                                                      (filler: "hang on, checking" while tools run)
                   {"type": "cards", "turn": "a1b2c3", "cards": [...]}  results to show under the reply
                   {"type": "done", "turn": "a1b2c3", "how": "Answered by Claude · used calendar"} reply
                     finished (turn: for a thumbs rating; how: one line on how it was answered)
                   {"type": "nudge", "kind": "...", "text": "...", "job_id": 3, "id": "ab12", "category":
                    "Investing", "level": "alert" | "notify" | "quiet"}   (level: pop up and speak / silent
                                                      notification / chat only; id: for a thumbs rating, sent
                                                      back as {"type": "rate_nudge", "id": "ab12", "up": true})
                                                      Buddy speaking up (job_id: a finished
                                                      background job; see GET /api/jobs/{id})
                   {"type": "whisper", "kind": "...", "text": "..."}
                                                      listen-in: translation / about_you / correction
                   {"type": "open", "links": [{"app": "uber://...", "url": "https://...", "label": "..."}],
                    "compare": "id"}                  open Uber / Lyft with a trip filled in (the
                                                      first now, the next when Buddy is back in front);
                                                      with compare, the ride reader reads both prices
                   {"type": "form", "action": "read|scroll|fill", "id": "...", "fills": [...]}
                                                      the form helper: read / fill the browser form; the
                                                      phone answers {"type": "form_screen" | "form_result"}
                   {"type": "error", "text": "..."}
```

- Settings: `BUDDY_COMPANY_DOCS`, `BUDDY_DIAGNOSTICS`, `BUDDY_FRAGMENT_WAIT`, `BUDDY_INTEL_API_KEY`, `BUDDY_MODULES`, `BUDDY_PROACTIVE_LOOP`, `BUDDY_TRACE`
- Endpoints: `GET /api/files/{file_id}`, `GET /health`, `GET /admin/agents`, `POST /admin/agents/{name}/{action}`, `GET /admin/connectors`, `GET /admin/modules`, `GET /admin/pricebook`, `POST /admin/pricebook/rebuild`, `GET /admin/glasses`, `POST /admin/glasses/test` and 170 more
- Uses: activity.py, addressee.py, advisor.py, agent.py, agents.py, ambient.py, attention.py, bankfeed.py, benchmarks.py, benefits.py, bip.py, bodylog.py, brain.py, browse.py, budgets.py, builder.py, calls.py, card_optimizer.py, cftc.py, checkin.py, claudefix.py, codejob.py, companydocs.py, compliance.py, config.py, connecthub.py, contacts.py, convolog.py, costwatch.py, crossmarket.py, dealcollector.py, dealforecast.py, dealmail.py, deals.py, diagnostics.py, dietcheck.py, dietplan.py, docread.py, evidence.py, factors.py, fhirlabs.py, finance.py, finops.py, finra.py, foodfacts.py, freshness.py, fundamentals.py, gateway.py, glasslink.py, guard.py, health.py, health_data.py, healthmodel.py, healthnow.py, healthpage.py, healthrecords.py, insights.py, invest.py, jobs.py, keytest.py, labs.py, labsetup.py, library.py, lifelog.py, liveears.py, location.py, maestro.py, maestro_usage.py, mailbrain.py, mailmap.py, makers.py, maps.py, markets.py, memory.py, merchants.py, messaging.py, model_advisor.py, modeltrain.py, modules.py, money.py, moodlog.py, newsdesk.py, paperlib.py, pausehabit.py, paymail.py, persona.py, plaid.py, prevent.py, prevention.py, pricebook.py, privacy.py, privacygate.py, proactive.py, recommend.py, research.py, researchdesk.py, review.py, rides.py, securitywatch.py, selfcheck.py, selfdeploy.py, social.py, stratlab.py, stratrepo.py, stratrules.py, techradar.py, themes.py, thinking.py, thrift.py, today.py, tools.py, trace.py, transit.py, travel.py, travel_saved.py, trends.py, tutor.py, usage.py, vocabulary.py, voiceprint.py, watchpicks.py, whentobuy.py, whisper.py

### glasslink.py

```text
The glasses door: where a pair of glasses joins Buddy's brain (the Devices and Link modules).

A pair of glasses does not reach this server by itself. The glasses runtime does, from the phone: it holds the
connection to the glasses, fits what Buddy sends to the lens or the ear, applies the privacy rule, and reports what
the wearer actually got (xr/src/link.js). This side keeps the session and gives the brain four tools:

  glasses_describe   what the connected glasses can do
  glasses_show       a title, some lines, and optionally an alert
  glasses_ask        a yes-or-no question; the result is the wearer's answer
  glasses_choose     a short list; the result is the one the wearer picked

The messages are the contract in docs/BWCP.md, version 0.2, over the WebSocket /ws/glasses (the same access check
as the phone's connection):

  from the runtime   hello (the device and its profile), result, state, said, problem, bye
  to the runtime     welcome | refused, call (one of the tools), cancel

The privacy rule is applied twice. The runtime applies all of it. This side applies the part that must hold even
if the runtime were wrong: content that may reach neither the lens nor the ear on the route the glasses declared
is never sent at all (health anywhere but a direct route; personal through another company's cloud). A device
that declares no route, or one this side does not know, is treated as the strictest.

Nothing about a session is kept after it ends, except one meter line a day for the device (modules.py).
NOT YET RUN WITH REAL GLASSES: tested against stand-ins for the runtime, and with the runtime in a browser.
```

- Settings: `BUDDY_GLASSES_WAIT`
- Uses: modules.py

## How it connects

- Relies on: Agents and skills; Buddy Connect; Buddy Intelligence Platform; Claude: the brain; Email, messages and apps; Fixing and explaining itself; Free AI models (Groq, Gemini, Mistral, Cerebras, GitHub Models, OpenRouter); Home and health; Insights and costs; Investing: signals, research and practice; Learning: the English and investing tutors; Making and doing things; Memory; Money; News, trends and research; Phone calls; Security and privacy; Shopping and deals; Speaking up on its own: briefing, reminders and alerts; The price book, pattern learning and trained models; Travel and getting around; Voice: Talk mode and Listen-in
- Relied on by: Claude: the brain

