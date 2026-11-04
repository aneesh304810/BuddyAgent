# Buddy Labs Biometric Information Privacy Policy

Source: https://claude.ai/artifact/L6oHPF6NiSatoRXAgg3Gyx, revision 7

Nov 4, 2026 · @Aneesh

## 1. What we collect

The only biometric data Buddy collects is a **voiceprint** of the user who turns on voice match. Nothing else, and never anyone else's. This policy is a draft for review by counsel.

- **What it is:** the user reads five short sentences once. Buddy turns each into 256 numbers that describe how the voice sounds. The audio itself is not kept.
- **Where it's made:** by a speaker-recognition model running on Buddy's own server. The voiceprint is never sent to an AI provider or any other company.
- **How it changes:** clips that clearly match update the voiceprint, so it follows how the user sounds over time. The user can also add a clip by saying "that was me".
- **What we don't collect:**
  - voiceprints of other people, including people heard in Listen-in mode;
  - face, fingerprint, iris or hand geometry;
  - stored voice recordings.

Audio sent for speech-to-text is used only to turn speech into words, never to identify anyone.

## 2. Purpose, notice and consent

The voiceprint has one purpose: to recognize the user's own voice in Talk mode, so Buddy answers only them and ignores other voices nearby. It is collected only after the user reads a written notice and gives written consent in the app.

- **Optional:** voice match is off by default. Buddy works without it, and refusing consent never limits any other feature.
- **Before collection,** the app tells the user in writing:
  - that a voiceprint will be collected and stored;
  - the specific purpose (above);
  - how long it's kept (section 4).
- **Written release:** the user agrees by ticking a box and tapping "I agree". Buddy records the date, time and policy version of the consent, and keeps that record for as long as the voiceprint exists plus 3 years.
- **New purposes** need a new notice and new consent. The voiceprint is never used for advertising, profiling, or decisions about the user.
- **Withdrawal:** the user can withdraw consent at any time (section 5). The voiceprint is then deleted.

## 3. No sale, no sharing, strong protection

Buddy Labs never sells, leases, trades or otherwise profits from a voiceprint, and never discloses it to anyone, except as the law requires.

- **No disclosure** unless the user consents to a specific disclosure, it completes a transaction the user asked for, or a valid warrant or subpoena, or the law, requires it.
- **Same or better protection than other sensitive data:**
  - stored only in Buddy's encrypted database (AES-256);
  - kept on a server with no public ports;
  - covered by MFA, role-based access and quarterly access reviews;
  - included in encrypted backups, which are deleted after 14 days.

  See the Information Security Program.
- **Staff access:** no one at Buddy Labs looks at a voiceprint, except to fix a problem the user reports.

## 4. Retention and destruction

A voiceprint is permanently destroyed at the **earliest** of the events below, and never kept longer than 3 years after the user's last interaction with Buddy.

| Event | Destroyed within |
| --- | --- |
| The user says "forget my voice", or turns voice match off | Immediately in the database; backups age out within 14 days |
| The user withdraws consent or asks for deletion | Immediately; backups within 14 days |
| The user deletes their account, or Buddy is shut down | 30 days |
| Voice match unused for 12 months (the purpose is no longer being served) | 30 days after the 12 months |
| 3 years after the user's last interaction with Buddy | On that date at the latest |

**How it's destroyed:** the voiceprint record is deleted from the encrypted database, with secure delete on so the freed space is overwritten. Encrypted backups containing it expire on their normal 14-day cycle. The consent record (date and version only, with no biometric data) is kept as proof of consent.

## 5. Your rights and the in-app consent text

Users can see, withdraw and delete their voiceprint at any time, and contact us with questions.

- **In the app:** Settings → Voice match → Forget my voice. Or just say "Buddy, forget my voice."
- **By email:** privacy@buddylabs.io, subject "Biometric request". We act within 30 days.
- **Where this policy lives:** it is published on the Buddy Labs website and linked from the consent screen.

**Consent screen text** (shown before "Teach Buddy my voice"):

> **Voice match uses your voiceprint** To recognize your voice, Buddy will create a voiceprint: numbers describing how your voice sounds, made from five sentences you read. This is biometric information.
>
> - **Why:** only to recognize your voice in Talk mode, so Buddy answers you and ignores other voices.
> - **Where:** stored encrypted on Buddy's server. Never sold or shared, and never used for ads.
> - **How long:** until you turn voice match off or say "forget my voice", after 12 months without use, or at most 3 years after you last use Buddy. Whichever comes first.
>
> Voice match is optional. Buddy works without it.
>
> ☐ I have read the Biometric Information Privacy Policy, and I consent to Buddy Labs collecting, storing and using my voiceprint as described.
>
> **\[I agree\]**   **\[Not now\]**

## 6. Laws covered, open items and history

This policy is written to meet the strictest US biometric law, Illinois BIPA, so it also covers the lighter ones. Counsel should confirm it before public launch.

| Law | Key requirement | Covered by |
| --- | --- | --- |
| Illinois BIPA (740 ILCS 14) | Public written policy with retention and destruction rules; written notice and release before collection; no sale; no disclosure; reasonable care | Sections 2–5 |
| Texas CUBI (Bus. & Com. Code 503.001) | Consent; no sale; destroy within a reasonable time, at most 1 year after the purpose ends | Sections 2–4 (12-month rule) |
| Washington (RCW 19.375) | Notice and consent before enrolling for a commercial purpose; no sale | Sections 2–3 |
| Colorado Privacy Act biometric rules | Written policy, retention schedule, consent, deletion | Sections 2–5 |
| California CCPA/CPRA (when thresholds are met) | Biometrics are sensitive data; notice and limits on use | Sections 2–3; Privacy Policy |

**To do before public launch** (so every statement above is true in the product)

- [ ] Consent screen with the section 5 text, shown before enrollment; record consent date, time and policy version
- [ ] Turning voice match off deletes the voiceprint (today: only "forget my voice" does)
- [ ] Automatic deletion after 12 months without voice match use, and at 3 years after last interaction
- [ ] Secure delete switched on for the database, so deleted voiceprints are overwritten
- [ ] Publish this policy on the website and link it from the consent screen and the Privacy Policy
- [ ] Counsel review, including whether Listen-in creates any biometric data (it does not create voiceprints today)

**Revision history**

| Date | Change | By |
| --- | --- | --- |
| 4 November 2026 | First version | Aneesh Nair |
