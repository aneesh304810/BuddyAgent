# Your health plan with Buddy: Oura Ring, goals, food and tracking

Buddy reads your Oura Ring (and the phone's Health Connect data), helps you set health goals, turns them into a
few daily habits and food targets, tracks them automatically, and nudges you at the right moments. Everything stays
on your Buddy server. It's coaching, not medical advice.

> **Changed on 17 November 2026 (Privacy Gateway).** Health information is no longer sent to any AI model, Claude
> included. Buddy still syncs the ring and the phone and still tracks habits by rules, but it no longer discusses
> your health in conversation: asking "how did I sleep?" shows your numbers on a card with no interpretation, and
> the health plan can no longer be set up or changed by talking to Buddy. The parts of this guide that describe
> those conversations describe how it worked before. Use the Health page instead (below). See
> `docs/PRIVACY-GATEWAY.md`.
>
> **Health as ranges.** If you switch it on, on the server (`sudo bash /opt/buddy/deploy/set.sh BUDDY_HEALTH_RANGES`,
> then type `on`), Buddy can again talk about your sleep, readiness and activity, working from ranges against your
> own usual ("sleep below usual this week") and never from the numbers. You still see the numbers on a card.

## The Health page

**More → Health** in the app (`web/health.html`). It shows your own numbers to you, worked out by fixed rules on
your server. No AI model is given any of it.

- **Today:** readiness, sleep, heart rate variability, lowest night heart rate and temperature, what they mean for
  the day (the same fixed rules the morning line uses), today's habits with an "I did this" button, and when the
  ring and the phone were last read ("Read the ring now" reads it at once).
- **Trends:** each measure over 14, 30 or 90 days as a line, with your usual drawn across it (the middle value of
  the days before this week) and whether the latest day and this week are below, about or above your usual. A
  usual needs seven earlier days of readings.
- **Habits:** each habit over the last 7 and 30 days (days done, the rate, the run in a row), and your weekly
  averages now against when the plan started.
- **Food:** what you noted today against the plan's calories and protein, and the last week's meals. You type a
  meal; calories and protein are counted only if you type them, because nothing estimates them any more.
- **Plan:** goals, up to eight habits (tracked from the ring or phone with a target, or ticked off by you), and
  food targets. This form replaces the set-up conversation in section 2. "Forget my health plan" (pressed twice)
  deletes the plan, the habit log and the meals; the readings stay until their 90 days are up.

What it does not do: it gives no advice beyond those fixed rules, and it is not medical advice.

## 1. Connect your Oura Ring

Two parts: a one-time setup on the server by whoever runs Buddy, then a tap in the app for each person.

**Once per server (about 5 minutes).** Oura ended personal access tokens in December 2025, so Buddy uses Oura's
sign-in (OAuth), like other apps.
1. On the server: `cd /opt/buddy && sudo git pull && sudo bash deploy/update.sh`, then
   `sudo bash /opt/buddy/deploy/oura.sh`. It prints this server's redirect URI, for example
   `https://buddy.your-tailnet.ts.net/connect/oura/linked`.
2. Open **cloud.ouraring.com/oauth/applications** (or **developer.ouraring.com**), sign in, **New Application**:
   - **Display name:** `Buddy`; **Description:** `Personal assistant reading the user's own data`
   - **Website / Privacy policy / Terms:** your Buddy privacy policy link
   - **Redirect URI:** the one `oura.sh` printed, exactly
   - **Scopes:** tick everything offered (Buddy only reads)
3. Paste the **Client ID** and **Client Secret** into `oura.sh` (hidden). Buddy restarts.

The redirect URI is the server's own page on your private Tailscale network: after sign-in Oura sends the browser
straight back to Buddy, so there's no code to copy and it never passes through anyone else's site.

**Each person (30 seconds).** Buddy app → **Menu → Connections → Health → Oura Ring → Connect** (or ask Buddy
"connect my Oura ring"). Sign in to Oura, allow access, and the page says "Oura Ring is connected". Back in the app it
shows Working. The phone must be on the Tailscale network (it is, if the Buddy app works).

Buddy syncs the ring every 3 hours (and before the morning briefing). Ask "how did I sleep?" to check.

**If it says "Connect again":** tap **Reconnect**. Oura's refresh tokens work once; Buddy keeps the newest itself,
so this is only needed if access was removed in the Oura account or an old server backup was restored.

## 2. Set up your plan (a 5-minute chat)

Say: **"Let's set up my health plan."** Buddy looks at your last weeks of sleep, steps and readiness, then asks:
- your goals (lose fat, more energy, sleep better, run a 10K, lower resting heart rate...) and why;
- your current routine and how much time you have;
- food: what you like, allergies, what you avoid, how you cook;
- any condition, injury or medication (it'll tell you what to check with a doctor).

It proposes a small plan: 2–3 goals, 3–5 habits that start near where you are (not a leap), and food targets
(calories and protein). It saves it only when you say yes. Change it any time: "make it 9,000 steps", "drop the
water habit".

Habits Buddy tracks by itself: **steps**, **sleep hours**, **bedtime** (lights out by a time), **workouts a week**,
**active minutes**. Others (water, stretching, no sugar after 8) you tick off by telling Buddy: "done my water".

## 3. Day to day

- **Morning briefing:** readiness and sleep, what that means today ("low recovery: keep it easy"), today's habits.
- **Evening:** if steps are behind, how long a walk closes the gap.
- **Before bed:** a wind-down reminder 30–40 minutes before your bedtime target.
- **Sunday evening:** the week's results and an offer to adjust.
- **Food:** tell Buddy what you ate ("had a paneer wrap for lunch"); it estimates calories and protein. Ask "what
  should I eat for dinner?" and it answers from what's left for the day, your preferences and allergies, and can
  suggest places nearby (searching by your neighbourhood, never your exact position).
- **Progress:** "how am I doing this week?" or "this month?": each habit's days done, streaks, and your averages
  against where you started.
- Nudges follow quiet hours and wait while you're driving or in a meeting.

## 4. Privacy

- Oura data and Health Connect data: 90 days on your server, then deleted. Only Claude sees them, never a free model.
- The plan stays until you change it; the habit log and meals 90 days.
- **"Forget my health plan"** deletes the plan, habit log and meals.
- To stop Oura syncing: remove Buddy in your Oura account (Connected apps). To turn Oura off for the whole server,
  clear the `BUDDY_OURA_*` settings with `set.sh` (press Enter).
