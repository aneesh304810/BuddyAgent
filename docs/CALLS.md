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
5. *Optional, a different voice:* Voices → Voice Library → filter female, English (American),
   "Conversational" → **Add to my voices** → **Copy voice ID**.
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
- it never pays, cancels or agrees to fees;
- no recording.

What each call costs (estimated, part by part: line, listening, voice, Claude) is in the app's **Insights →
Phone calls**, and the monthly totals per service are under **Connections → Calls**. The prices are estimates set
from the providers' price pages; if yours differ, set `BUDDY_CALL_PRICE_LINE_MIN`, `BUDDY_CALL_PRICE_LISTEN_MIN`
or `BUDDY_CALL_PRICE_VOICE_1K` with `set.sh`.

Rough cost:
- a 5-minute appointment call: about $0.15–0.30, with the voice about $0.06–0.10 of that;
- a 45-minute hold: about $0.30–0.60, because nothing goes to Deepgram or Claude while nobody is talking.

## 8. If something's wrong

| Problem | Fix |
|---|---|
| `calls.sh`: Tailscale refused Funnel | Add the policy line from step 4, then run `calls.sh` again |
| A Test says 401 or 403 | That key is wrong. Run `calls.sh` again, paste the right one, press Enter for the rest |
| Buddy says "Calls aren't set up yet" | Run `calls.sh` again; the message names what's missing |
| The call doesn't go through | Plivo console → Logs → Calls shows why (credit, number, or a blocked destination) |
| The phone rings but Buddy is silent | A wrong voice or model ID: clear `BUDDY_CALL_VOICE` / `BUDDY_CALL_VOICE_MODEL` (run `set.sh` and press Enter). Still silent: run `journalctl -u buddy -f` during a test call and send the lines that mention `call` |
| The other side sees "Spam Likely" with your own number | Run `calls.sh` again and answer **N** to use the Plivo number |

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
