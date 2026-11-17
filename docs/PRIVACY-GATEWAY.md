# Privacy Gateway

One check that every request to an AI model passes before it leaves the server (`backend/buddy/privacygate.py`).
Decided by the owner on 17 November 2026 ("option 1").

## The rules

| What | Claude | Every other model |
|---|---|---|
| The owner's health information | Never. Cut out of the request, or the request is refused | Never. Refused |
| Other personal details (name, mail, money, documents, any key or password) | Yes | Never. Refused |
| Public text | Yes | Yes |
| The check fails or cannot decide | Refused | Refused |

A refused request is never retried on another model. There is no setting that turns the gateway off.

Health information means: measurements (sleep, readiness, HRV, heart rate, temperature, weight, lab values),
symptoms, conditions, medicines, allergies, what was eaten, health goals, and anything worked out from them. A
question that names no person ("what does HDL cholesterol mean?") is public.

## Health as ranges (the one setting)

Off unless the owner switches it on, on the server: `sudo bash /opt/buddy/deploy/set.sh BUDDY_HEALTH_RANGES`, then
type `on` (or `off`). There is no switch in the app and no endpoint that changes it.

When on, the health-data tool gives Claude, and only Claude, lines like

    [health range] sleep duration: latest below usual; week about usual; trend falling; 5 of 7 days below usual, 0 above

built by rules in `healthranges.py` from a fixed list of measures and words. "Usual" is the owner's own middle value
over the earlier days, so the line holds no value, unit, date, name or age. The gateway lets a marked line through
only if it matches that exact form; a marked line that says anything else is cut like any health text. The health
plan, symptoms, medicines and food are never sent, in this mode or any. Each time ranges are given it is recorded.

What ranges still reveal: that the owner tracks these measures and which way each is going. The request also goes
out under the owner's own account, so the provider can tell it all comes from one person. Asking the provider for
zero data retention, or running a model on the owner's own hardware, are the ways to reduce that; neither is built.

## Where information can reach a model, and what guards it

| Door | Used by | Guard | Proven by |
|---|---|---|---|
| `config.claude_client()` (the only place a Claude client is made) | 36 call sites in 26 files: the chat, mail, money, forms, calls, research, tutor, briefings, background jobs | `sanitize_request` runs on the request body just before it is sent; a body that cannot be read is refused | `test_every_claude_request_passes_the_gateway_at_the_client`, and the static check that only `config.py` makes a client |
| `free_models._post` (the only send to the free models) | general tasks, labelling, the free-model pool | `authorize_request`: public text only | `test_the_free_models_door_refuses...` |
| `maestro.ask_with` (OpenAI, added providers, the pool, Claude) | intelligence jobs: headlines, papers, strategy rules, news checks | `authorize` before any model is called; a refusal ends `maestro.ask` | `test_maestro_checks_before_any_model_is_called`, `test_a_privacy_refusal_is_final...` |
| Health tools (`my_health`, `health_plan`) | the chat | `withhold`: the result goes to a card on the owner's screen and a marker goes to the model | `test_in_the_chat...`, `test_a_health_tools_result...` |
| The chat's incoming message | `agent.Conversation.reply` | a message about the owner's own health is answered by Buddy itself, is not sent on, and its words are not kept in the conversation | `test_in_the_chat...` |
| Conversation history and memory lines | every later Claude request | covered by the first door: old turns and remembered facts are cut the same way | `test_a_claude_request_has_health_cut_out...` |
| Agent delegation, background jobs | all use `claude_client()` or Maestro | covered by the first three doors | the static check |
| Meaning search (`brain.py`) | memory search | runs a small model on this server; nothing is sent out | by inspection, no test |

## How health is recognised

- **By source.** A health tool's result is health information whatever it looks like.
- **By content.** Rules over the text: a health measure with a number beside it; the owner (I, my, "the user", their
  name) beside a symptom, condition, medicine or test; the field names health data is stored under. No AI model
  takes part in the decision.

## The API

| Function | What it does |
|---|---|
| `classify(payload, memory, owner_name)` | level (0 Public to 3 Restricted), the kinds found, the reasons (never the content) |
| `health(text)`, `personal(text, memory)`, `secret(text)` | the detectors |
| `authorize(provider, payload, source)` | returns the classification, or raises `PrivacyBlocked` |
| `authorize_request(provider, body, source)` | the same for a whole request body |
| `sanitize_request(body, source)` | cuts health out of a Claude request in place; returns how many pieces |
| `withhold(tool, result)` | what a tool's result becomes before a model may see it |
| `healthranges.on()`, `brief(memory)`, `allowed(line)` | the one setting; the range lines; the check a marked line must pass |
| `record(...)`, `status(memory)` | the record and the Privacy Gateway page (`GET /api/privacy`, admin only) |

## Not covered (known gaps)

1. **Speech.** Spoken words are turned into text by a speech model before any rule can read them, so a spoken
   sentence about health reaches that speech model. The text that comes back is checked. Accepted by the owner on
   17 November 2026; no work planned.
2. **Pictures and files inside a request.** A photo of a lab report, or a PDF sent to Claude as a file, cannot be
   read by these rules and is not stopped. Left for later: see `docs/BACKLOG.md`.
3. **Wording no rule was written for.** A symptom in unusual words, a misspelling, another language, a number with
   no label, or encoded text can pass. The source rule (health tools) does not depend on wording; the content rules do.
4. **False stops.** A harmless sentence that looks like health is cut or refused.
5. **Email and documents about health.** A health appointment email or an insurance letter is read by Claude as
   mail; only lines the content rules recognise are cut.
6. **Keys and passwords in text bound for Claude** are not blocked by the gateway (they are refused for every
   other model). Existing masking covers card and identity numbers.
7. **The health plan (coach).** Its results are withheld and health messages are not sent on, so it cannot be set up
   or discussed in conversation any more. Its rule-based tracking still runs.
8. **No per-app policy editor and no custom rules yet.** The page shows the rules and the record; it does not edit them.
9. **Not run on the server yet.** Tested here only.
