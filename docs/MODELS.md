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

The log lives in your encrypted database for 90 days; previews are 280 characters at most
with card and ID numbers masked. It's also `GET /admin/insights?days=7`.

The Costs page also has a **Which model answered** table: turns answered by Claude quick and
main, how often quick handed over, and calls to each free model (and how often they were all
busy). It's in `GET /admin/costs` under `routing`, too.
