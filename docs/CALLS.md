# Buddy's phone agent: full setup

Buddy calls businesses for you (doctors' offices, shops, card companies), as your assistant, after your yes.
Plivo carries the call, Deepgram hears the other side, Claude decides what to say, ElevenLabs speaks it.
Nothing is recorded; only a short outcome summary is kept (180 days).

Never paste a key in chat. The setup scripts ask for each one with hidden typing.

## 1. Plivo: the phone line (about 10 minutes)

1. Sign up at **plivo.com**.
2. **Add credit**: about $20 is plenty. A trial account can only call numbers you've verified, so it can't call
   your doctor.
3. **Buy a number**: Phone Numbers → Buy Numbers → United States → Voice enabled → pick one (a local area code
   is best). It costs about $0.50 a month.
4. **Note** the **Auth ID** (starts with MA) and **Auth Token** from the console's Overview page.
5. *Optional: show your own mobile as caller ID.* Look for **Verified Caller IDs** (under Voice or Phone
   Numbers), add your mobile, and enter the code Plivo sends.

## 2. Deepgram: hears the other side (about 3 minutes)

1. Sign up at **console.deepgram.com**. New accounts get free credit.
2. **API Keys → Create a New API Key** → role **Member** → copy the key.

## 3. ElevenLabs: Buddy's voice (about 5 minutes)

1. Sign up at **elevenlabs.io**. The **Free** plan includes 20,000 v4 Turbo characters a month (about 8–12
   test calls); **Starter** (about $5 a month) gives 150,000.
2. Ignore the **Agents** settings (webhooks, secrets, auth connections): Buddy doesn't use them.
3. Left menu → **Developers → API Keys → Create API Key**, named `Buddy`. Permissions:
   - Text to Speech: **Access**
   - User: **Read**
   - Voices: **Read**
   - everything else (Speech to Text, Agents, Music, Dubbing, …): **No access**
   - credit limit, if offered: for example 20,000 a month
4. Copy the key (it's shown once).
5. *Optional, a different voice:* Voices → Voice Library → filter by gender (male or female), English, and
   "Conversational" → listen → **Add to my voices**. Then pick it in one of two ways:
   - **In the app, by listening** (easiest): ask Buddy *"show me soft male voices for calls"*. It lists the
     voices on your ElevenLabs account that fit, each with ElevenLabs' own description and a link to hear a
     sample. Say *"use Will"* and calls speak with it from the next call. *"Go back to the default voice"*
     undoes it. Then *"call me"* to hear it on a real call.
   - **On the server:** **Copy voice ID** in ElevenLabs, then `sudo bash /opt/buddy/deploy/set.sh
     BUDDY_CALL_VOICE` and paste it. A voice chosen in the app wins over this one.

   A voice has to be on your account (the ready-made ones, or ones you added) to be used. If the chosen voice
   is ever refused, the call carries on with a voice your account certainly has, looked up once a day, since
   ElevenLabs retires its ready-made voices from time to time.
   How Buddy *talks* (playful, formal, brief) is not the voice: tell Buddy, e.g. *"be funny with me"*. That
   applies when it talks to you, in the app and on calls to you; on calls to other people it stays polite and
   to the point.
6. The voice model is chosen automatically: at the first call each day, Buddy asks ElevenLabs which models your
   account has and uses the newest **Turbo** model, the cheapest one made for live conversation (v4 Turbo,
   $0.04 per 1,000 characters), falling back to Flash. If the key's permissions list **Models**, give it
   **Read**, so Buddy can ask.

## 4. Tailscale: let Plivo reach Buddy (about 2 minutes)

Tailscale admin console → **Access controls** → add this line to the policy and save (it's also in
`deploy/tailscale-policy.hujson`):

```
"nodeAttrs": [{"target": ["tag:buddy-server"], "attr": ["funnel"]}],
```

Only `/calls` on port 8443 becomes reachable from the internet. Every request there needs that call's random
code and Plivo's signature. Buddy's app stays private on 443.

## 5. On the server (about 5 minutes)

```
cd /opt/buddy && sudo git pull && sudo bash deploy/update.sh
sudo bash /opt/buddy/deploy/calls.sh
```

`calls.sh` installs the call engine (about 550 MB, a few minutes the first time), then asks, with hidden typing:

| It asks for | You enter |
|---|---|
| Plivo Auth ID, Auth Token | from step 1 |
| Plivo phone number | for example `+12015550100` |
| Deepgram API key | from step 2 |
| ElevenLabs API key | from step 3 |
| Your full name | as businesses should hear it |
| Your mobile number | the callback number, and where hold calls ring you |
| Date of birth | for example `January 2, 1990` (shared on appointment calls only) |
| Health insurance | for example `Aetna PPO, member ID W123456789` (appointment calls only) |
| Show your own number? | **y** only if you verified it in step 1.5; otherwise **N** |

Press Enter to keep any value that's already saved. At the end it prints the public address and restarts Buddy.

Optional settings (each asks for its value, hidden, and restarts Buddy):

```
sudo bash /opt/buddy/deploy/set.sh BUDDY_CALL_VOICE          # voice ID from step 3.5
sudo bash /opt/buddy/deploy/set.sh BUDDY_CALL_VOICE_MODEL    # only to force one model; Enter clears it
sudo bash /opt/buddy/deploy/set.sh BUDDY_CALL_STT            # nova-3 to go back to the older hearing model
```

How Buddy keeps the conversation natural: Deepgram **Flux** hears the other side and decides when they've finished
speaking (from their words and tone, not just silence), so Buddy answers in about a second; the voice uses ElevenLabs'
**Flash** model, the quickest to start speaking; replies start with a short sentence. If calls ever go silent on
Buddy's side after it greets (it can't hear), set `BUDDY_CALL_STT` to `nova-3` and tell Claude Code.

## 6. Check it

1. Buddy app → **Menu → Connections → Calls** → **Test** on Plivo, Deepgram and ElevenLabs. All three should say ok.
2. **Call yourself first:** "Call my mobile 201-… and ask what time it is." Approve it, answer, and talk to it.
   Listen to the voice and how quickly it replies.
3. Save the Plivo number in your contacts as **Buddy**. Hold-and-connect calls ring you from it.

## 7. Using it

Ask in plain words. Buddy always says what it will share and **waits for your yes**.

- **Appointment:** "Call Dr. Mehta's office at 201-555-0123 and book my annual checkup, weekday mornings next
  week." It checks your calendar, may share your name, callback number, date of birth and insurance, and
  confirms the date and time back to them.
- **Question:** "Call Trader Joe's in Hoboken and ask if they're open on Thanksgiving."
- **Hold and connect:** "Call Chase card services and get me a person about a disputed charge." It goes through
  the menu and waits on hold. When a person answers, it says "one moment, connecting you," notifies the app
  and rings your mobile. You take over and do the account verification.
- **Call me:** "Call me", "call me with my briefing" or "call me so we can talk." Buddy rings your own mobile
  and on that call it is the **whole Buddy**, as in the app: your memory, mail, calendar, money and every tool.
  Ask "anything in my email?" or "move my 3 o'clock" while you walk. Anything that needs your yes waits for a
  spoken "yes" on the call. "Bye", "that's all" or "I'm good" ends it; a plain "okay" or "yes" never does.
  Up to 20 minutes. To turn this off and keep "call me" to the message only: `BUDDY_CALL_BRAIN=off`.
- **Results:** Buddy messages you when a call ends. Ask "how did my calls go?" for the last five.
- **Who's calling:** turn on **Settings → Share contacts** in the app (opt-in; names and phone numbers only,
  sent to your server every 6 hours). Calls Buddy answers for you then greet a contact by name and treat them as
  known, "what's Priya's number?" looks them up, and "reply to Priya" finds her chat even when it shows only a
  number. Contacts never go to free models or the logs. Turning the switch off deletes them from the server.

Built-in rules:
- at most 5 calls a day;
- 15 minutes a call (90 for hold calls, 20 for calls to you);
- it says it's an AI assistant if asked;
- it never gives card or account numbers, SSN or passwords;
- it never pays or agrees to fees, and it cancels nothing, except a subscription you asked it to cancel
  (the cancel service: one yes per cancellation, see MONEY.md);
- no recording.

What each call costs (estimated, part by part: line, listening, voice, Claude) is in the app's **Insights →
Phone calls**, and the monthly totals per service are under **Connections → Calls**. The prices are estimates set
from the providers' price pages; if yours differ, set `BUDDY_CALL_PRICE_LINE_MIN`, `BUDDY_CALL_PRICE_LISTEN_MIN`
or `BUDDY_CALL_PRICE_VOICE_1K` with `set.sh`.

Rough cost:
- a 5-minute appointment call: about $0.15–0.30, with the voice about $0.06–0.10 of that;
- a 45-minute hold: about $0.30–0.60, because nothing goes to Deepgram or Claude while nobody is talking.

### Languages
A call can be held in ten languages: English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese,
Italian and Dutch. Buddy starts in English and changes when the person it reaches speaks another of them, and
changes back when they do.

- **Hearing.** One Deepgram model (Flux Multilingual) hears all ten and says which language each turn was in.
  It leans towards English until someone speaks another language, so English with a heavy accent is not taken
  for a different language. If it does name another language but the words came back as English, Buddy treats
  the turn as English.
- **Speaking.** Buddy decides which language to answer in from the person's last turn. The voice follows the
  language Buddy's words are written in, with the same voice in every language.
- **What stays the same.** It introduces itself the same way, shares only what it may, and says it is an AI
  assistant when asked, in whatever language. A recorded menu that offers another language ("Para español,
  oprima el dos") does not change the call's language. The summary you get is always in English.
- **Not covered:** Malayalam, Tamil, Telugu, Kannada and other languages outside the ten. The hearing model
  does not have them.
- **Settings** (`set.sh`): `BUDDY_CALL_LANGUAGES` narrows the list (`en` keeps every call in English, as before;
  `en,es` allows Spanish only). `BUDDY_CALL_LANGUAGE_HINTS` changes what the hearing leans towards at the start
  (`en` by default; `en,es`; or `none` to lean nowhere).
- Not yet tried on a real call in another language: the first few are worth listening in on.

The same engine now hears Talk mode in the app as you speak (docs/MODELS.md, "Live hearing"), so the Deepgram
key set here is used there too.

## 8. If something's wrong

| Problem | Fix |
|---|---|
| `calls.sh`: Tailscale refused Funnel | Add the policy line from step 4, then run `calls.sh` again |
| A Test says 401 or 403 | That key is wrong. Run `calls.sh` again, paste the right one, press Enter for the rest |
| Buddy says "Calls aren't set up yet" | Run `calls.sh` again; the message names what's missing |
| The call doesn't go through | Plivo console → Logs → Calls shows why (credit, number, or a blocked destination) |
| The phone rings but Buddy is silent | A wrong voice or model ID: clear `BUDDY_CALL_VOICE` / `BUDDY_CALL_VOICE_MODEL` (run `set.sh` and press Enter). Still silent: run `journalctl -u buddy -f` during a test call and send the lines that mention `call` |
| The other side sees "Spam Likely" with your own number | Run `calls.sh` again and answer **N** to use the Plivo number |
| A long pause before Buddy answers | `sudo bash /opt/buddy/deploy/calllog.sh` shows, for each turn, how long each step took and which was slowest (below) |

### Where a call's time goes
Every turn of a call writes one line to the server's log, counted from the moment the other person stopped
speaking. No words are logged. Read them with:

```
sudo bash /opt/buddy/deploy/calllog.sh          # calls in the last 2 hours
sudo bash /opt/buddy/deploy/calllog.sh 30       # the last 30 minutes
sudo bash /opt/buddy/deploy/calllog.sh live     # watch a call as it happens
```

A line looks like this:

```
call 12 timing turn 2 (tell_me): they spoke 2.0 s; +0.30 s pause noticed; +0.80 s words from Deepgram [en];
  +0.80 s Claude asked; +2.60 s first words written (Claude); +3.00 s voice started. Slowest step: Claude, 1.80 s
```

| Step | What it is | If it is the slow one |
|---|---|---|
| pause noticed | Buddy's speech detector heard them stop | Fixed at 0.3 s |
| words from Deepgram | The hearing gave the words and judged the turn finished; `[en]` is the language it heard | A short "Hello?" leaves it unsure for up to 0.9 s. Much more than that: Deepgram or the connection to it |
| Claude asked | The question went to Claude | Later than the words only when Buddy was still busy with the turn before |
| first words written | Claude's first sentence was ready ("the ready greeting" and "a filler" need no Claude) | The model, or on a call to you with Buddy's whole brain, the first reply, which reads all of Buddy's instructions and tools |
| voice started | ElevenLabs' audio began on the line: the pause the caller hears ends here | ElevenLabs, or the server too busy to send audio on time |

The script ends with a count of which step was slowest and the five longest waits. The first line of each call
says which hearing and voice models it used.

## 9. Buddy answers the calls you miss

Once set up, a call you don't pick up goes to Buddy instead of your voicemail. Buddy answers like a secretary
("Hi, you've reached Aneesh's phone, I'm the assistant..."), finds out who's calling and why, and:
- **a real caller:** takes the message (name, reason, dates, callback number) and tells you in the app; family,
  doctors, real fraud alerts and "call back today" are marked **urgent**. Callers Buddy knows (from what you've told
  it, your emails and calendar) hear specific timing from your calendar ("in a meeting until three"); unknown callers
  hear only general timing. Nobody hears where you are or anything personal;
- **a scam or robocall:** "Not interested, thanks", hangs up, and only counts it ("how did my calls go?" shows today's);
- **sales:** a short note, low priority.

If someone asks, Buddy says honestly that it's an AI assistant. At most 30 answered calls a day, 5 minutes each.

**Turn it on:** `calls.sh` prints the codes with your Plivo number. On your phone, dial each and press Call:
- `**61*1PLIVONUMBER**20#`: when you don't answer within 20 seconds
- `**67*1PLIVONUMBER#`: when you're on another call
- `**62*1PLIVONUMBER#`: when your phone is off or has no signal

**Turn it off** (back to voicemail): `##004#`. Buddy links its Plivo number to itself when it starts; nothing else to
set up. On a Plivo trial account, only calls from numbers you've verified in Plivo get through.
