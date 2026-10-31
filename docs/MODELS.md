# Which model answers: Claude router and free models

Buddy picks the cheapest model that does each job well. Two parts, both on the server
only (no app reinstall).

## 1. Claude router (on by default)

| Model | Gets |
|---|---|
| Haiku 5.5 (quick) | Small talk, reminders, lists, quick facts. About 20x cheaper than Sonnet. |
| Sonnet 5.5 (main) | Email, money, places, travel, planning, writing, anything long, and short follow-ups to those. |
| Opus 5.5 (expert) | Hard questions, through the existing "ask the expert" step-up. |

Rules decide, so there's no extra model call and no delay. If Haiku finds a question needs
more, it hands it to Sonnet in the same turn (`hand_over`), so you never get a weak answer.

Settings (optional):

| Setting | Default | Meaning |
|---|---|---|
| `BUDDY_ROUTER` | `1` | `0` sends every turn to Sonnet. |
| `BUDDY_FAST_MODEL` | `claude-haiku-5-5` | The quick model. |

## 2. Free models for general work (off until you add a key)

Claude can hand **general** tasks to a free-tier model: translate public text, summarize a
public article, rewrite or polish general text, brainstorm, explain a concept, a general
quick answer, or classify text.

**Your private data never goes to them.** Anything about you (email, money, health,
location, contacts, plans, messages, memory) stays on Claude. This is enforced in code, not
just asked for: Buddy refuses to send text that contains an email address, a phone number,
card or ID numbers, or any name or phrase from your memory, and Claude does that task itself.

**Picked by skill, then speed.** Each kind of task has a best-first list (Gemini for long
text, summaries and languages; Mistral for rewriting and writing; Groq and Cerebras for fast
quick answers and labels). Buddy learns each provider's speed and reliability as it goes:
quick tasks go to whichever is fastest lately, quality tasks to the most capable, and a flaky
provider drops back. It tries at most three, with short timeouts; a provider at its free limit
rests until the limit resets. If all are busy, Claude Haiku does it.

### Simple questions: straight to a free model

Simple, general, timeless questions don't need Claude at all: *"what is a black hole"*, *"why do
cats purr"*, *"how do you say thank you in Portuguese"*. When free models are on, Buddy sends
these straight to the best free model for the job and speaks its answer (no Claude call). It
stays on Claude when the question is about you, your things or anything current or local
(*my*, *today*, *news*, *price*, *weather*, *near*...), when the privacy guard finds personal
details, or when the free model says it isn't sure. Your 👍/👎 on these answers shows how good
they are in Insights. `BUDDY_FREE_DIRECT=0` turns it off.

### Coding: DeepSeek for small questions, Claude for the real work

Buddy decides in code, before anything is sent, which coding tasks a free model may take:

| Goes to DeepSeek (free, through OpenRouter) | Stays on Claude |
|---|---|
| A snippet, a regex, a one-liner, a small function | Refactoring, design, anything across several files |
| What an error message means | Security: auth, encryption, permissions, injection |
| Explaining a concept or a library call | Live systems: deploys, migrations, databases, servers, `sudo`, cron |
| Converting between languages or formats | Hard bugs: race conditions, leaks, flaky behaviour |
| | Buddy itself, its agents, and the agent builder |
| | More than about 120 lines or 8,000 characters of code |
| | Anything with a key, token, password, private key, `.env` line or your server's address |

Then two checks keep the final answer good:

1. **Automatic check:** Python code in a free answer must parse, and refusals don't count.
   A bad answer moves on to the next model (GitHub's GPT, then Gemini...), then Claude Haiku.
2. **Claude reviews it:** a free answer comes back to Claude marked "check it before using it".
   Claude fixes anything wrong, or does the task itself, before you hear it.

DeepSeek is used only through OpenRouter (the `BUDDY_OPENROUTER_KEY`), never DeepSeek's own
API, which keeps data in China. Its model is `BUDDY_DEEPSEEK_MODEL` (default
`deepseek/deepseek-chat-v3-0324:free`); OpenRouter renames free models often, so if it stops
answering, pick the current free DeepSeek model from openrouter.ai/models and set it.

### Turn it on (phone, Termux, SSH into the server)

Get one or more free keys (each takes a minute; the more you add, the faster and more
reliable it gets):

| Provider | Where | Setting |
|---|---|---|
| Google Gemini | aistudio.google.com → Get API key | `BUDDY_GEMINI_KEY` |
| Groq | console.groq.com → API Keys | `BUDDY_GROQ_KEY` |
| Mistral | console.mistral.ai → API Keys (free "Experiment" plan) | `BUDDY_MISTRAL_KEY` |
| Cerebras | cloud.cerebras.ai → API Keys | `BUDDY_CEREBRAS_KEY` |
| GitHub Models (OpenAI GPT, free) | github.com → Settings → Developer settings → Fine-grained tokens → new token with the **Models: read** permission | `BUDDY_GITHUB_MODELS_KEY` |
| OpenRouter | openrouter.ai → Keys | `BUDDY_OPENROUTER_KEY` |

**ChatGPT / OpenAI models:** the ChatGPT app's free plan doesn't include the API, and the
OpenAI API itself is pay-as-you-go with no free tier. The free way to use OpenAI's models is
**GitHub Models** (GPT-4.1 mini by default, free with a GitHub account, with daily limits).
Groq and Cerebras also host OpenAI's open-weight `gpt-oss` models for free: set
`BUDDY_GROQ_MODEL` to `openai/gpt-oss-120b` to try it.

```
sudo bash /opt/buddy/deploy/update.sh
sudo bash /opt/buddy/deploy/set.sh BUDDY_FREE_MODELS     # type 1
sudo bash /opt/buddy/deploy/set.sh BUDDY_GEMINI_KEY      # paste (hidden)
sudo bash /opt/buddy/deploy/set.sh BUDDY_GROQ_KEY        # paste (hidden)
```

Paste keys only into that hidden prompt, never into a chat.

Free tiers may use what you send to improve their models, which is one more reason only
general, non-personal text goes to them.

### Model names

Defaults: `gemini-flash-latest`, `llama-3.3-70b-versatile` (Groq), `mistral-small-latest`,
`llama-3.3-70b` (Cerebras), `openai/gpt-4.1-mini` (GitHub Models), `openrouter/free`. Providers rename models often; if one stops
working, set its current name, e.g. `set.sh BUDDY_GROQ_MODEL`. The other settings are
`BUDDY_GEMINI_MODEL`, `BUDDY_GITHUB_MODEL`, `BUDDY_MISTRAL_MODEL`, `BUDDY_CEREBRAS_MODEL`, `BUDDY_OPENROUTER_MODEL`.

## 3. Web search and reading pages (Serper + free models + Claude)

With the Serper key (docs/KEYS.md), Buddy searches and reads the web itself instead of
letting Claude pull whole pages into its context:

1. **google_search** (Serper): Google's ranking, answer box, News and Maps, about $1 per
   1,000 searches, against $10 per 1,000 for Claude's web_search.
2. **read_pages**: Buddy downloads up to 3 pages (https, public addresses only, 3 MB and
   12 seconds each), drops menus, ads and scripts, keeps tables.
3. A free model pulls out only the facts that answer the question (exact figures copied),
   a few hundred tokens instead of 5,000 to 50,000.
4. Claude writes the answer. `google_search` with `read_top` does steps 1 to 3 in one go.

Claude reads the trimmed page itself (no digest) when the question has anything personal
in it, the free model is unsure or busy, the page is mostly a table of numbers, or it
asks for `full`. Claude's own web_search and web_fetch stay as the fallback: pages Buddy
can't open (blocked, PDFs, JavaScript-only) and when Serper isn't set up. Chat, research
jobs and the expert all work this way. Insights → **Web reading** shows pages read,
digested, failed, and the tokens and dollars saved.

Settings: `BUDDY_WEB_DIGEST=0` turns the free-model digest off (Claude gets trimmed pages);
`BUDDY_PAGE_RAW_CHARS` (default 8000) is how much of a page Claude gets without a digest;
`BUDDY_FETCH_MAX_TOKENS` (default 12000) caps Claude's own web_fetch.

## 4. Hearing you: Groq's Whisper for Talk mode

With `BUDDY_GROQ_KEY` set (the same free key as the Groq free model), what you say in
**Talk mode** is turned into text by Groq's hosted Whisper large-v3-turbo: about 0.3 to 0.6
seconds instead of 1.5 to 3 on a small server, and far better with Malayalam and mixed
speech. **Listen-in (other people's voices) never goes to Groq**: it stays on your server's
own Whisper. If Groq is down, at its free limit (Buddy rests it for a minute), or hears a
language outside "My languages", your server's Whisper does that clip instead.

- At Groq's free limit, the app (speech recognition on Automatic) switches Talk to the phone's
  own recognizer (Samsung's or Google's) until the limit resets, then back; it says so in the chat.
- `BUDDY_WHISPER_TALK=local` keeps all speech on your server.
- `BUDDY_GROQ_WHISPER_MODEL` (default `whisper-large-v3-turbo`).
- `BUDDY_GROQ_PAID=1` counts the paid rate (about $0.04 an hour of audio, at least 10 s a
  request) in Insights; on the free tier it shows $0.
- Insights → **Hearing you** shows clips by Groq and by your server. The English tutor's
  pronunciation scores come from your server's Whisper only, so they're rarer with Groq on.

## 5. Voice match: Talk mode answers only you

Settings → **Teach Buddy my voice**: read five sentences (5 seconds each). The server turns them
into a voiceprint (a small speaker-recognition model, sherpa-onnx, about 25 MB, installed by
update.sh / `deploy/voice.sh`). In **Talk mode** every clip is compared with it **before**
transcription: your voice goes on; anything else (the TV, people around you) is ignored, never
transcribed or sent to Groq. When Buddy isn't sure, it stays quiet. Clips under a second (a
"yes", "thanks") pass only right after Buddy spoke to you. Clips that clearly match keep
refining the voiceprint; "Buddy, that was me" adds one it wrongly ignored.

- Needs the server to hear the audio: Speech recognition on Automatic or Server (with voice match
  on, Talk stays on the server even at Groq's free limit, using its own Whisper).
- `BUDDY_VOICE_THRESHOLD` (default 0.5): higher is stricter.
- The voiceprint is biometric data: it stays in Buddy's encrypted database; "forget my voice"
  (Settings or by voice) deletes it. Listen-in is unaffected.

**Speech detection on the phone:** the app decides what's speech with Silero VAD (MIT licence,
`android/app/src/main/assets/silero_vad.onnx`, about 2 MB, ONNX Runtime), not loudness: breathing,
rustling and hum aren't sent at all, quiet voices in Listen-in are caught, and a clip ends when the
speech does. If the model can't load, the old loudness check takes over.

**Quick Settings tile:** add "Buddy" to Quick Settings (swipe down, edit). Off: tap opens Buddy and starts
Talk (Android only lets the microphone start while Buddy is on screen, so after a reboot this is
the quick way). On: tap switches Talk and Listen-in.

**Switching modes by voice:** "Talk mode", "Listen-in mode", "go to sleep", "wake up" switch at
once (no Claude call). **Phone closed → Listen-in** (Settings, on by default): with a headset on,
folding the phone shut (the hinge sensor, so it works even with the cover screen on) or locking it
switches Talk to Listen-in; opening it switches back.

## See it working

**Insights** (in the Android app's header, and on the web page) shows every model request:

- Totals: model spend, requests and failures, typical reply time (and the slowest 5%), free-model
  calls, how much of each prompt came from cache, and what routing saved.
- Spend per day (tap a bar for its numbers).
- **What it was for**: talking with you, Listen-in, expert, research, email sorting, general
  tasks... with the cost, request count, typical time and which models did the work.
- **Each model**: requests, failures, cost, typical and slowest time, tokens in and out.
- **Latest requests**: tap one to see its tokens, the tools it used, and a short preview of what
  was asked and answered. Tap a purpose or model above to filter the list.

**Accuracy and your rating.**

- *Free answers right*: Claude reviews every free model's answer and records whether it used it
  as given, fixed it or redid it (no extra wait: it's noted in the same step as the reply).
  Insights shows "right as given" and "usable" per model and task, plus answers that failed the
  automatic check. Buddy uses this itself: a free model Claude keeps correcting for a kind of
  task is tried later for it.
- *Your rating*: 👍 / 👎 under each reply in the chat (tap again to take it back), or say "that was
  wrong" / "great answer". It counts for every model that worked on that reply.

**Speed (from your side)**: time to Buddy's first words and to the end of the reply (typical and
slowest 5%), replies that failed or were interrupted, how often "one sec" was needed, how often
the quick model handed over, speed per model route, and which tools the slowest replies used.

**How it's doing: the daily health check.** Early each morning Buddy compares the last 7 days
with the week before: speed, failures, models failing or at their free limit, free-model
accuracy, your thumbs down, hand-overs, cost per reply, cache reuse, the month's forecast, and a
reconciliation that every dollar in the money tally is in the request log. Each finding says
what to fix (a setting, a tool, or where in the code to look). Reports are kept for a year, so
the trend shows. **Check now** runs it any time.

**The daily digest.** Around 9am (outside quiet hours) Buddy writes the analysis in plain words
in your chat: how the week went, and up to three recommendations. Tap it to open Insights. If no
phone is connected it waits. Settings: `BUDDY_HEALTH_DIGEST_AT` (hour, default `9`),
`BUDDY_HEALTH_DIGEST=0` to switch it off. Ask "how are you doing?" any time for the same.

**Download** (web Insights): every request, or reply timings, as CSV, to study or to share
when asking for a fix. Also `GET /admin/insights/export?table=calls|turns&days=30`,
`GET /admin/health` and `POST /admin/health/run`.

The log lives in your encrypted database for 90 days; previews are 280 characters at most
with card and ID numbers masked. It's also `GET /admin/insights?days=7`.

**Free models** in Insights lists every provider: on, key set but off, or no key; its model;
what it's first choice for; and its requests, speed and accuracy. **Tools and agents** lists every
tool used for your replies: Buddy's own, agents (Zapier, Gmail, built ones) and Claude's server
tools (web search, page fetching, code), with calls, failures, typical time, extra cost (web
searches; Zapier tasks if `BUDDY_ZAPIER_PLAN_USD` and `BUDDY_ZAPIER_TASKS_PER_MONTH` are set) and
the last error.

The Costs page also has a **Which model answered** table: turns answered by Claude quick and
main, how often quick handed over, and calls to each free model (and how often they were all
busy). It's in `GET /admin/costs` under `routing`, too.
