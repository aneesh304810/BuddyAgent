# Your health plan with Buddy: Oura Ring, goals, food and tracking

Buddy reads your Oura Ring (and the phone's Health Connect data), helps you set health goals, turns them into a
few daily habits and food targets, tracks them automatically, and nudges you at the right moments. Everything stays
on your Buddy server and only Claude sees it. It's coaching, not medical advice.

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
