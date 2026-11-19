# Voice: Talk mode and Listen-in

Hearing you (live as you speak through the call engine, or in clips by Whisper on your server or Groq), optional noise removal, knowing when you talk to Buddy, your voice match, how you pause, the words it should hear right, and Listen-in's whispers.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_voice_match["voice_match"]
        t_learn_word["learn_word"]
    end
    subgraph Modules["Modules"]
        whisper_py["whisper.py<br/>Whisper on the user's own server (faster-whisper), and for Talk mode,…"]
        liveears_py["liveears.py<br/>Live hearing for Talk mode, on the same engine the phone calls use"]
        denoise_py["denoise.py<br/>Noise removal for Talk mode's live hearing: on this server, and only…"]
        ambient_py["ambient.py<br/>Buddy hears the people around the user and whispers when it matters"]
        addressee_py["addressee.py<br/>Is the user talking to Buddy, or to someone else in the room?"]
        voiceprint_py["voiceprint.py<br/>in Talk mode, only the user's own voice counts"]
        pausehabit_py["pausehabit.py<br/>How the owner pauses and interrupts while speaking, learned per user…"]
        transcripts_py["transcripts.py<br/>what Listen-in heard, kept as text for 2 days, if you switch it on"]
        vocabulary_py["vocabulary.py<br/>names, places and terms from your life, and words you corrected"]
    end
    you --> t_voice_match
    t_voice_match --> voiceprint_py
    you --> t_learn_word
    t_learn_word --> vocabulary_py
    liveears_py --> denoise_py
    liveears_py --> whisper_py
    ambient_py --> transcripts_py
    ambient_py --> whisper_py
    voiceprint_py --> whisper_py
    db[("Your database<br/>room_lines, saved_transcripts")]
    transcripts_py --> db
    o_0["Buddy Connect"]
    whisper_py -. uses .-> o_0
    o_1["Claude"]
    whisper_py -. uses .-> o_1
    o_2["Free AI models"]
    whisper_py -. uses .-> o_2
    o_3["Insights and costs"]
    whisper_py -. uses .-> o_3
    o_4["Making and doing things"]
    whisper_py -. uses .-> o_4
    o_5["Memory"]
    whisper_py -. uses .-> o_5
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `voice_match` | Voice match for Talk mode: Buddy only answers the user's own voice there | status, turn_on, turn_off, forget, that_was_me | voiceprint.py |
| `learn_word` | Speech recognition misheard a word (the user corrects you, or a name came out garbled and you worked out what they meant): save the right spelling so it's recog | - | vocabulary.py |

## Technical design, module by module

### whisper.py

```text
Speech-to-text: Whisper on the user's own server (faster-whisper), and for Talk mode,
Groq's hosted Whisper large-v3-turbo when BUDDY_GROQ_KEY is set: about 0.3-0.6 s instead of
1.5-3 s on a small server, and far better with Malayalam. Only what the user says to Buddy in
Talk mode goes to Groq; Listen-in (other people's speech) always stays on this server.
BUDDY_WHISPER_TALK=local keeps everything here. If Groq is down, slow, over its free limit or
hears a language outside "My languages", the local model does it instead.

The phone records short utterances and sends the raw audio (16 kHz, mono,
16-bit); this turns them into text and detects the language, so nothing is sent
to Google's recognizer. Optional: install it with deploy/whisper.sh. The model
size is BUDDY_WHISPER_MODEL ("base" suits a 2 GB server; "small" is more
accurate but needs about 4 GB and is slower).
```

- Settings: `BUDDY_GROQ_KEY`, `BUDDY_GROQ_PAID`, `BUDDY_GROQ_WHISPER_MODEL`, `BUDDY_KNOWN_LANGUAGES`, `BUDDY_TALK_DROP_LOGPROB`, `BUDDY_UNSURE_LOGPROB`, `BUDDY_WHISPER_BEAM`, `BUDDY_WHISPER_MODEL`, `BUDDY_WHISPER_TALK`
- Uses: budgets.py, config.py

### liveears.py

```text
Live hearing for Talk mode, on the same engine the phone calls use.

Talk mode used to work in clips: the phone waited for a silence, sent the whole clip, the server checked the voice,
turned it into text, sent the text back, and held any sentence that looked unfinished for three seconds or more
before answering. Here the phone sends your speech while you are still saying it. Deepgram's Flux (through Pipecat,
as on calls) writes the words as they come and decides that you have finished from what you said, not from a timer:
a complete sentence is answered at once, an unfinished one is given time.

  phone  -> {"type": "live", "op": "hello", "lang": "auto"}          Talk mode is on (or its language changed)
  server -> {"type": "live_state", "on": true, "end_ms": 3200}       live hearing can be used (end_ms: how long the
                                                                      phone waits in silence before ending a turn
                                                                      itself)
  phone  -> {"type": "live", "op": "start", "id": "a1", "pcm": ...}  you started speaking (with the half second before)
  phone  -> {"type": "live", "op": "audio", "pcm": ..., "sp": true}  more of it, a tenth of a second at a time
  server -> {"type": "live_partial", "text": "what's the wea"}       the words so far
  server -> {"type": "heard", "text": ..., "engine": "live", ...}    you finished: the same message a clip gets
  phone  -> {"type": "live", "op": "end"}                            the phone's own silence limit passed first
  phone  -> {"type": "live", "op": "bye"}                            Talk mode is off

What goes where: your Talk-mode speech goes to Deepgram, opted out of its model improvement programme (as on calls),
instead of Groq. Listen-in never comes here: other people's speech stays on this server. The words then take the
same road as before: voice match, the Privacy Gateway, Claude. Buddy still answers with the phone's own voice.

If live hearing can't be used (no Deepgram key, the call engine not installed, a language Flux doesn't have,
Deepgram unreachable or silent), the turn is heard the old way from the same audio, so nothing said is lost.
BUDDY_TALK_LIVE=off switches it off.

Noise removal (denoise.py) is a switch, off unless BUDDY_NOISE_CANCEL=talk: Deepgram advises sending speech
unaltered. When on, the audio Flux hears is cleaned on this server first; voice match and the clip fallback still
get what the microphone heard.
```

- Settings: `BUDDY_CALL_PRICE_LISTEN_MIN`, `BUDDY_DEEPGRAM_KEY`, `BUDDY_NOISE_CANCEL`, `BUDDY_TALK_EOT`, `BUDDY_TALK_EOT_TIMEOUT_MS`, `BUDDY_TALK_LIVE`
- Uses: calls.py, denoise.py, diagnostics.py, gateway.py, usage.py, whisper.py

### denoise.py

```text
Noise removal for Talk mode's live hearing: on this server, and only when you switch it on.

A small neural model (GTCRN, half a megabyte, MIT licence) run through sherpa-onnx, the library voice match already
uses. It takes your speech as it arrives and takes out the background (a fan, traffic, a restaurant's clatter), a
few hundredths of a second behind. Nothing leaves the server for this.

It is off unless you ask for it, for a reason. Deepgram, whose model does the hearing, advises against cleaning
audio before recognition: the cleaning takes parts of the speech with the noise, and its model was trained on noisy
audio, so recognition usually gets worse, not better. So Buddy sends your speech unaltered by default.

  BUDDY_NOISE_CANCEL   off (default) | talk: Talk mode's live turns are cleaned before they are heard

Voice match always gets the unaltered audio (it was taught on your unaltered voice), and a turn that falls back to
being heard as a clip is heard unaltered too. Phone calls are not covered: a phone line is 8 kHz and already
processed by the phone network. Install the model with: sudo bash /opt/buddy/deploy/noise.sh
```

- Settings: `BUDDY_NOISE_CANCEL`, `BUDDY_NOISE_MODEL`
- Uses: config.py, gateway.py

### ambient.py

```text
Listen-in mode: Buddy hears the people around the user and whispers when it matters.

It whispers for three reasons:
  - translation: someone speaks a language the user doesn't understand (anything
    outside settings.known_languages); Buddy gives just the gist in English
  - about_you:   in such a language, people are talking about the user, mocking
    them, or planning something that concerns them
  - correction:  someone (in any language) states something technically wrong
    with confidence; Buddy says what is actually correct

Speech is analysed live and kept only in memory, in a short rolling window.
Nothing said by other people is written to disk.

To keep always-on listening affordable, speech is gathered into batches
(settings.listen_batch_seconds) and a cheap model (settings.listen_triage_model)
first decides whether a batch could need a whisper. Only then does the listen
model read it closely. In a room speaking your languages, most batches stop at
the cheap first pass.
```

- Settings: `BUDDY_ROOM_MEMORY_MINUTES`
- Uses: agent.py, benefits.py, config.py, free_models.py, memory.py, privacy.py, thrift.py, transcripts.py, usage.py, whisper.py

### addressee.py

```text
Is the user talking to Buddy, or to someone else in the room?

In Listen-in, "Hey Buddy" always works. Beyond that, Buddy judges like a person
would: a sentence that sounds like a question or request for an assistant, or a
reply soon after Buddy spoke, is checked by the cheap model against the context
(what Buddy just said, how long ago, what the room is talking about). Everything
else is room talk and costs nothing. Answers that start this way still can't
approve anything: that always needs the Approve button or Talk mode.
```

- Uses: agent.py, config.py, privacy.py, usage.py

### voiceprint.py

```text
Voice match: in Talk mode, only the user's own voice counts.

The user reads five short sentences once (Settings -> Teach Buddy my voice). Each is turned into a
voiceprint: 256 numbers that describe how a voice sounds (a speaker-embedding model, about 25 MB,
on this server; no audio is kept). Every Talk clip is compared with it before transcription:

  - sounds like the user          -> transcribed and answered as usual
  - doesn't, or Buddy isn't sure  -> ignored, never transcribed or sent to Groq (stay quiet when unsure)
  - too short to judge (< 1 s)    -> accepted only right after Buddy spoke to the user ("yes", "thanks")

Clips that match well nudge the voiceprint, so it follows how the user sounds over time; "Buddy, that
was me" adds the last ignored clip. Listen-in is unaffected. The voiceprint is biometric data: it stays
in Buddy's own (encrypted) database and "forget my voice" deletes it. Install: deploy/voice.sh.
```

- Settings: `BUDDY_VOICE_MODEL`, `BUDDY_VOICE_THRESHOLD`
- Uses: config.py, memory.py, whisper.py

### pausehabit.py

```text
How the owner pauses and interrupts while speaking, learned per user and kept as a setting.

A sentence cut off by a pause is held back for a moment so the rest can join it. How long to hold is learned:
each time the rest of a sentence arrives, the gap is folded into a running average; interrupting Buddy mid-reply
is counted too. Stored under the setting key "pause_habit" (JSON), so it can be read back and shown.
```

### transcripts.py

```text
Room transcripts: what Listen-in heard, kept as text for 2 days, if you switch it on.

OFF by default. Recording other people's conversations without their consent is illegal in many
places (several US states need everyone's consent; Brazil's LGPD and India's DPDP Act may apply),
so switching it on is your call: say "keep room transcripts" (or "stop keeping room transcripts").

  - Every line Listen-in hears is kept as text (never audio), masked and encrypted, for 2 days
    (BUDDY_ROOM_KEEP_DAYS), then deleted automatically.
  - "Save the conversation from 3 to 4pm" copies that stretch into saved transcripts, kept until
    you delete them, with a title.
  - Ask for a stretch any time ("the transcript from this morning", "yesterday 2 to 3pm"), or as
    a text file in the chat.
  - "Delete room transcripts" clears the log (saved ones stay unless you say so).

Speech recognition can't tell speakers apart and makes mistakes, and your own words are in it too.
```

- Tables: `room_lines`, `saved_transcripts`
- Settings: `BUDDY_ROOM_KEEP_DAYS`
- Uses: makers.py, memory.py, privacy.py, tools.py

### vocabulary.py

```text
Words Buddy should hear right: names, places and terms from your life, and words you corrected.

Speech recognition stumbles on names (Miraflores, Priya, Nubank) and on fast, accented or mixed
speech. This list primes it: Whisper on the server gets it as a prompt, and the phone's
recogniser as biasing words (Android 13+). It grows by itself: names in Buddy's memory, and
every time you correct a misheard word ("no, I said Barranco"), Claude records it with learn_word.
```

- Settings: `BUDDY_VOCABULARY`
- Uses: config.py, memory.py

## How it connects

- Relies on: Buddy Connect; Claude: the brain; Free AI models (Groq, Gemini, Mistral, Cerebras, GitHub Models, OpenRouter); Insights and costs; Making and doing things; Memory; Money; Phone calls; Security and privacy
- Relied on by: Claude: the brain; Insights and costs; Learning: the English and investing tutors; Security and privacy; Speaking up on its own: briefing, reminders and alerts; The server and the app

