# Buddy Maestro

The one gateway for Buddy's AI models: which providers there are, what each may see, which one does each
job, what it cost, and how to add another later. In the app: Intelligence, the **Maestro** tab. In chat:
"which models are you using".

## The privacy rule

**Anything about you goes to Claude and nowhere else.** No fallback, no added provider and no setting
changes that. If Claude cannot answer, a personal task waits; it is never sent to another provider.

It is checked in code in one place (`maestro.may_see`). Text that is meant to be public is also checked
for personal details before it leaves (email addresses, phone numbers, secrets, things from your memory).
If it has any, it goes to Claude instead.

## Providers

| Provider | Role | Sees your data? |
|---|---|---|
| Claude (Anthropic) | Everything about you, and the main assistant. Three tiers: cheap, mid, strongest | Yes, the only one |
| OpenAI | Optional and paid. Public records only, for jobs you assign to it. Off until you set its key, model id and price | No |
| Mistral, Cerebras, Google (Gemini), NVIDIA | The free pool for public work, tried in the order each is best at | No |
| OpenRouter | Fallback only: used when none of the pool answers | No |
| Ollama (local) | A place kept for a model on a machine you control. Not set up | No |
| Groq | Speech to text only. Not used for text | Voice clips only |
| Providers you add | Public work only, for the jobs you assign to them | No |

GitHub Models is no longer used: it kept hitting its limit.

Each provider shows whether it is set up, working, failing or switched off, with its last problem, read
from Buddy Connect's record of its calls. Switching one off on the Security page stops it here too.

## Adding a provider later

No code change is needed for any provider that speaks the common chat format (a `/chat/completions`
address), which most do.

1. On the Maestro tab, under **Add a provider**, enter a short name, its address (must start with
   `https://` and be a public host), the model's id from the provider's own page, and its price per million
   tokens in and out (0 and 0 if free).
2. On the server, enter its key with the hidden prompt: `sudo bash /opt/buddy/deploy/set.sh BUDDY_AI_NAME_KEY`
   (the tab shows the exact name).
3. Run the 48-headline test on it, and if it scores well, assign a job to it.

An added provider can never see your data, counts toward the monthly spending limit if it has a price, and
can be removed from the tab. Up to ten can be added.

NVIDIA is new in the pool: its free key is `BUDDY_NVIDIA_KEY`, and its model can be changed with
`BUDDY_NVIDIA_MODEL`. Its address and default model were written from its documentation and have not been
run against the real service yet.

## Finding a model, and getting a key

- **Find a model for a task.** Pick a kind of task (sort and label, pull out names and facts, summarise
  long texts, analyse and write, code, hard questions) or search by name. Maestro lists the models that fit
  (a price ceiling for the task, enough room for the text, and what the model is tuned for), with the price
  per million tokens in and out, how much text it takes, and how Buddy can use it: straight from its maker
  if that provider is in the lineup, otherwise through the fallback provider or by adding its maker.
- **Where the prices come from.** A public list of models and prices, read without a key (only a request
  for the list is sent). The date it was read is shown, and "Refresh the price list" reads it again. Check
  the maker's own page before relying on a price.
- **Price is not quality.** Lists are cheapest first, except hard questions, which are dearest first. That
  is a starting point only: run the 48-headline test before giving a model a job.
- **Anything about you** shows no list: Claude only.
- **Get a key.** Each provider has a link to its own key page and the command to enter the key on the
  server. Buddy cannot sign up for you or create a key: each provider needs you to make the account and
  accept its terms.
- **Check a key.** Asks the provider to list its models with the key that is set. It says whether the key
  works; the key itself is never shown.

In chat: "which model is best for summarising long documents", "how much does X cost", "is my Mistral key
working".

## Jobs, the limit and the test

Each job has its own model choice: the free pool, one named pool provider, a Claude tier, OpenAI, or an
added provider. A paid choice stops at the monthly limit ($5 to start) and the job goes back to the free
pool. A choice that fails or gives an unusable answer falls back to the free pool, unless the work is
personal. Any choice can be scored on 48 headlines with known right labels before a job is moved to it.
Details are in INTELLIGENCE.md.

## What goes through it today, and what does not yet

- **Through the Maestro:** the Intelligence Platform's jobs (labelling headlines, writing up
  convergences, finding companies and relationships).
- **Using the same pool and the same privacy check, but called directly:** Buddy's general free-model work
  (translating or summarising a public page, small talk), which is in `free_models.py`.
- **Not through it:** the main conversation, which is always Claude and chooses its tier in `router.py` and
  `routing.py`.

So the registry, the rule and the page cover every provider, but only the first group is routed by job
here. Moving the other two behind the same front door is the next step, and is a larger change because the
main conversation uses tools and streaming.
