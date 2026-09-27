# Buddy: your AI best friend in your earbuds

Buddy is a personal voice assistant in the spirit of JARVIS. You talk to it through
your earbuds and it talks back. It remembers your life and speaks up when something
matters. In **Listen-in** mode it quietly whispers translations, warns you when
people talk about you, and corrects technical nonsense in the room.

![Buddy on a phone](docs/screenshot.png)

## What works today (v0.1)

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
| **Listen-in mode** | 🌐 translates Hindi/Malayalam/Marathi/Tamil into English, 👀 tells you when people are talking about or mocking you, 💡 corrects wrong technical claims. Say "Buddy, …" to ask it something without leaving the mode. |

It works on **both iPhone and Android** today as a web app you can add to your
home screen. Audio goes through whatever the phone uses, so connect your earbuds and go.

## Architecture

```
 Earbuds ─BT─ Phone (web app now, native app later)
                │  speech→text (on phone)   text→speech (on phone)
                │  WebSocket: sentences both ways
                ▼
          Buddy backend (Python, FastAPI) ── Claude (brain + tools + web search)
            ├─ agent.py      conversation loop, streaming, interruption
            ├─ ambient.py    Listen-in analysis (translate / about you / correction)
            ├─ proactive.py  reminders + morning briefing pushed to the phone
            ├─ memory.py     SQLite: facts, reminders, goals
            └─ tools.py      what Buddy can do
```

## Run it

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # add ANTHROPIC_API_KEY, BUDDY_TOKEN, your name & timezone
uvicorn buddy.server:app --host 0.0.0.0 --port 8000
```

The phone browser needs **HTTPS** to use the microphone. The easiest options:
- For a quick test, `cloudflared tunnel --url http://localhost:8000` (or ngrok) gives you an https URL.
- To keep it always on, deploy the backend to Fly.io, Railway, Render or a small VPS.

On your phone, open `https://<your-url>/?token=<BUDDY_TOKEN>` once; the page
remembers the token. Connect your earbuds and tap the orb. Chrome on Android and
Safari on iOS both work. Use "Add to Home Screen" for an app-like icon.

Tests: `cd backend && pytest`

## Listen-in mode: good to know

- Browser speech recognition handles one language at a time, so pick the **room
  language** (English, Hindi, Malayalam, Marathi or Tamil). Automatic multi-language
  detection comes with the cloud speech-to-text upgrade in the roadmap.
- Browser speech-to-text can't tell speakers apart. Buddy knows this and stays
  cautious ("I think they said…").
- Buddy analyses other people's speech live and keeps it only in a two-minute
  in-memory window. It is never written to disk.
- Laws on recording or processing conversations differ by country and state.
  Use it in conversations you are part of, and check the rules where you live.

## Roadmap

The native app plan is in [`mobile/README.md`](mobile/README.md). In short:

1. **Native app (Flutter, iOS + Android):** background listening, a "Hey Buddy" wake
   word, and earbud long-press to talk (on Android, set Buddy as the default assistant;
   on iOS, use the Siri Shortcut "Hey Siri, Buddy").
2. **Better voice:** cloud streaming speech-to-text with automatic language
   detection and speaker separation, plus a natural neural voice for Buddy.
3. **Connectors:** Google Calendar, Gmail, WhatsApp/SMS (Android), Spotify and
   smart home. Buddy asks you to confirm before anything that sends messages or spends money.
4. **Smarter proactivity:** calendar-aware nudges ("leave in 10, traffic's
   building"), an evening wrap-up and goal check-ins.
5. **Voice ID:** Buddy only obeys your voice.
