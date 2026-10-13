# Buddy: your AI best friend in your earbuds

Buddy is a personal voice assistant in the spirit of JARVIS. You talk to it through
your earbuds and it talks back. It remembers your life and speaks up when something
matters. In **Listen-in** mode it quietly whispers translations, warns you when
people talk about you, and corrects technical nonsense in the room.

![Buddy on a phone](docs/screenshot.png)

## What works today (v0.2, phase 0)

| Feature | How |
|---|---|
| **Talk** | Tap the orb and talk. Replies stream sentence by sentence, so Buddy starts speaking in about a second. |
| **Hands-free conversation** | Buddy keeps listening between turns, so you don't need to tap again. |
| **Interrupt Buddy** | Just start talking over it. It stops and follows you. |
| **Personality** | Casual best friend: honest, funny, pushes you toward your goals (`backend/buddy/persona.py`). |
| **Memory** | Remembers facts you tell it ("I parked on level 3") and uses them later. |
| **Reminders** | "Remind me to call Mom at 6". At 6, Buddy chimes and says it in your ear. |
| **Goals & coaching** | "My goal is to finish the MVP this week". Buddy checks in on it. |
| **Morning briefing** | Spoken daily at `BUDDY_MORNING_BRIEF` (default 08:00). |
| **Web search** | News, weather, scores, facts (Claude's built-in web search). |
| **Listen-in mode** | 🌐 gives you the gist when people speak a language you don't know (anything outside `BUDDY_KNOWN_LANGUAGES`: English, Hindi, Malayalam, Marathi, Tamil by default), 👀 tells you when people are talking about or mocking you in that language, 💡 corrects wrong technical claims in any language. Say "Buddy, …" to ask it something without leaving the mode. |

| **Specialist agents** | Buddy orchestrates agents that are MCP servers (`agents.yaml`). Example agent: `worldclock`. Each agent is the template for new ones (`backend/agents/worldclock`). |
| **Approvals** | Agent tools marked `approval_required` do nothing until you say yes in a later message; Claude cannot approve its own actions. |
| **Kill switch, limits, audit** | Pause any agent by voice or admin API; daily call limits per agent; every agent call logged with arguments masked. |
| **Privacy** | Card, account, Aadhaar and PAN numbers, OTPs, CVVs and passwords are masked before anything reaches Claude. Database encrypted with SQLCipher. |
| **Budget** | Claude usage and cost tracked; Buddy pauses at `BUDDY_MONTHLY_BUDGET_USD`. |

It works on **both iPhone and Android** today as a web app you can add to your
home screen. Audio goes through whatever the phone uses, so connect your earbuds and go.

## Architecture

```
 Earbuds ─BT─ Android phone (web app now, native app later)
                │  Tailscale private network + HTTPS (no public ports)
                ▼
          Your private server (VPS, India)
            Buddy orchestrator (Python, FastAPI) ──masked text──▶ Claude
            ├─ agent.py      conversation loop, streaming, interruption, approvals
            ├─ agents.py     agent registry: MCP clients, approvals, limits, audit, kill switch
            ├─ privacy.py    masking of sensitive numbers
            ├─ usage.py      Claude cost tracking and monthly budget
            ├─ ambient.py    Listen-in analysis
            ├─ proactive.py  reminders and morning briefing
            └─ memory.py     encrypted SQLite (SQLCipher): facts, reminders, goals, audit, usage
          Specialist agents (MCP servers): worldclock now; money, mail, tutor… next
```

## Put it on your private server

Follow [`docs/SETUP.md`](docs/SETUP.md): about an hour, with the monthly cost
breakdown (about $22–45 a month in total).

## Run it locally (development)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env               # add ANTHROPIC_API_KEY and your name
cp agents.example.yaml agents.yaml
uvicorn buddy.server:app --port 8000
```

Tests: `cd backend && pytest`

## Listen-in mode: good to know

- Browser speech recognition handles one language at a time, so pick the **room
  language**: for example Kannada, Telugu, Arabic or Chinese to get the gist, or
  one of your own languages for technical corrections only. Automatic language
  detection comes with the cloud speech-to-text upgrade in the roadmap.
- Browser speech-to-text can't tell speakers apart. Buddy knows this and stays
  cautious ("I think they said…").
- Buddy analyses other people's speech live and keeps it only in a two-minute
  in-memory window. It is never written to disk.
- Laws on recording or processing conversations differ by country and state.
  Use it in conversations you are part of, and check the rules where you live.

## Roadmap

| Phase | What |
|---|---|
| 0 ✅ | Private server, encryption, masking, orchestrator and agent registry |
| 1 | Money agent: card bill reminders, expense tracking, spending limits |
| 2 | Organiser: tasks, morning briefing, evening wrap-up |
| 3 | Gmail and Calendar |
| 4 | Native Android app: wake word, earbud button, always-on Listen-in |
| 5 | English tutor: grammar, American pronunciation, WhatsApp lessons |
| 6 | Research and flights |
| 7 | Learns your style |
| 8 | Agent builder |

The native app notes are in [`mobile/README.md`](mobile/README.md).
