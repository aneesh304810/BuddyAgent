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
your server. No AI model is given any of it. Five sections along the top; Trends, Labs and Plan have smaller
sub-tabs under them, and the page remembers where you were.

| Section | Sub-tabs |
| --- | --- |
| **Today** | (one page) readings, what they mean, today's habits, how you feel |
| **Trends** | Measures · Patterns · Mood |
| **Labs** | Results · Add a report · Portals · Checkups |
| **Plan** | Progress · Food · Menu · Goals & habits |
| **Learn** | Research · Supplements · Medicines |
| **Sources** | (one page) each source, and "Read the ring now" |

- **Today:** readiness, sleep, heart rate variability, lowest night heart rate and temperature, what they mean for
  the day (the same fixed rules the morning line uses), today's habits with an "I did this" button, and **How are
  you today?**: the mood journal's check-in (below).
- **Trends → Measures:** each measure over 14, 30 or 90 days as a line, with your usual drawn across it (the middle
  value of the days before this week) and whether the latest day and this week are below, about or above your
  usual. A usual needs seven earlier days of readings.
- **Your health now** (on Today): where you stand, each with its cut-off and source: BMI, waist, the week's home
  blood pressure, A1c, LDL, the 10-year heart and stroke risk, this week's cardio, strength and sleep. Under it:
  **To watch** (what's over a line, most urgent first), **What to do** (steps from published guidance: 5 to 10%
  weight loss when the BMI is over, the DASH way of eating and less salt for blood pressure, the Diabetes
  Prevention Program's goals for prediabetes, what would lower your heart risk most) and **Tests to ask your doctor
  about**, only the ones your own numbers call for (the guideline's starting tests when blood pressure is high;
  liver tests with extra belly fat; a lipid panel and creatinine when the risk estimate needs them; A1c; Lp(a)
  once). Worked out by Buddy's code; no AI model sees any of it; not medical advice.
- **Blood pressure** (Today, and Trends → Body & BP): type each reading (top, bottom, pulse), or let your cuff's app
  send them through Health Connect, each with its own time. Morning (4 am to noon) and evening (5 pm to 4 am) are
  kept apart. Once there are 6 readings over 3 days, the week's home average is set against the **2025 AHA/ACC
  guideline** (categories unchanged from 2017: normal under 120/80, elevated 120-129 and under 80, stage 1 130-139
  or 80-89, stage 2 140/90 or more; at home stage 2 starts at 135/85, which matches 140/90 in the office, per the
  ACC/AHA table of matching values), with what the guideline says next (stage 1: medicine when the PREVENT risk is
  7.5% or more, otherwise 3 to 6 months of lifestyle change first; stage 2: medicine), morning against evening, and
  this week against last. A reading of 180/120 or more is answered at once with the American Heart Association's
  advice (measure again after 5 minutes; call your doctor; call 911 with chest pain, shortness of breath, back pain,
  numbness or weakness, a change in vision or trouble speaking). "How to measure" has the AHA's steps.
- **Trends → Body & BP:** the readings (Delete for a typed one), **weight and waist** (typed in kg or lb, cm or
  inches, or from a scale through Health Connect; typed wins on a day), BMI against the WHO cut-offs or, with Asian
  ancestry, the WHO expert consultation's lower ones (overweight from 23, obesity from 27.5), the healthy weight
  range for your height, waist against the IDF's South Asian cut-off (90 cm men, 80 cm women) or the NIH's (102 and
  88 cm), waist to height (NICE: under 0.5), and how to measure your waist (NIH). **10-year heart and stroke risk
  (PREVENT):** the American Heart Association's PREVENT equations (2024; the 2025 blood pressure guideline uses them)
  from your age and sex, total and HDL cholesterol, kidney function (eGFR worked out from your creatinine with the
  race-free CKD-EPI 2021 equation), the week's home blood pressure, diabetes, smoking, and blood pressure medicine
  and statins (two boxes on Labs → Checkups). It shows the risk of a heart attack, stroke or heart failure, and of
  a heart attack or stroke alone, the guideline's 7.5% line, and what each change alone would do (a top number of
  120, non-HDL cholesterol of 135, not smoking). Until everything is in, it says what's still needed. For ages 30 to
  79 without heart disease. Checked against the published example (preventr: 14.7% and 9.2%). **Your targets**
  for the week, and **Delete everything typed here**.
- **This week** (Today, and Trends → Body & BP): cardio minutes (Oura's moderate time, and its vigorous time
  counted double, as the Physical Activity Guidelines count them; the phone's active minutes on days without the
  ring) against 150 a week, strength sessions against 2 (workouts recorded as strength training, or "I did strength
  today"), steps against 8,000 a day and sleep against 7 hours, each with how to catch up ("about 25 minutes of brisk
  walking a day for the next 4 days"), and "go easy today" when readiness or HRV is low. The targets can be changed.
  **Reminders, not notifications:** the morning briefing ends with what's missing this week (added by Buddy's code
  after the AI-written part, so no AI model sees your numbers), and the evening check-in says what's still open
  today.
- **Trends → Mood (your journal):** how you feel, in your own words. On Today you pick mood, stress and energy
  from 1 to 5 (any one is enough), what was going on (work, family, sleep, exercise, illness, travel, money,
  alcohol, social, alone time) and a short note of your own if you like. Several a day are fine; a day is their
  average. Trends → Mood shows the last 7 days' averages, what was going on most often, and every entry, each
  with a delete button, and "Delete every entry" (asked twice). The days also feed Patterns, below, as factors.
  No AI model is given any of it, the notes least of all; kept as long as the readings (until you delete them). It is not a
  diagnosis: if you feel low for a long time, or unsafe, talk to a doctor or call or text 988 (the US Suicide &
  Crisis Lifeline).
  - **The evening check-in, in chat or by voice.** On your first chat after 6 pm each day, Buddy answers your
    message as usual and then asks, in fixed words: mood, stress and energy from 1 to 5 ("4, 2, 3", "mood 4
    stress 2", or a word such as "good"); whether you've been in touch with friends or family (yes, a little, no);
    how rested you feel from 1 to 5; and anything on your mind. Your answers are read by code before anything
    else, go into the journal (marked "check-in"), and reach no AI model; the conversation Claude sees later keeps
    only a note that a check-in happened. Buddy then reads the day back in fixed sentences: what was noted, last
    night's sleep and readiness from the ring, and your week's average mood (with the 988 line on a hard day).
    "Skip" stops it and keeps what you said; a message that isn't an answer ends it and is handled as a normal
    message; words about how you are, instead of a number, are kept as the note and the question is asked again.
    It isn't offered when Buddy's own reply ended by asking you something (it waits for the next chat). Say
    "check in" any time to start it. It never sends a notification. Switch it off or on under Trends → Mood.
    Spoken answers pass through the speech recognizer first, like every spoken word; typed answers go only to
    your server.
- **Plan → Progress:** each habit over the last 7 and 30 days (days done, the rate, the run in a row), and your
  weekly averages now against when the plan started.
- **Plan → Food:** what you noted today against the plan's calories and protein, and the last week's meals. You
  type a meal, or **look up a food**: USDA FoodData Central's basic foods, foods as people eat them (with portions
  such as "1 idli" or "1 cup") and branded products, each with calories, protein, carbs and fat per 100 g. Pick a
  portion and **how many** (3 × "1 slice" from a scanned bread's label, 2 × "1 large" egg, 0.5 × "1 cup"), or
  choose Grams and type them, and "Add" puts that food into today's meals, named with the amount ("Italian Bread
  (3 × 1 slice, 96 g)"), with the calories and protein worked out by code from USDA's or the label's numbers. No AI estimates them. Food lookups need a free api.data.gov key, set once on the
  server: `sudo bash /opt/buddy/deploy/set.sh BUDDY_USDA_KEY` (sign up at api.data.gov; Buddy can't do it for you).
  - **Does it fit?** Pick a food and its portion, and before you tap Add Buddy's code checks it against your own
    plan: **Fits your plan**, **Fits, with care** (with what to watch) or **Not for you** (it's on your allergy
    list). It checks:
    - *your allergies and foods to avoid* (typed on Plan → Goals & habits), against the food's name, its ingredients where USDA or
      Open Food Facts list them, and a pack's allergen list, with the words that give each away ("dairy" finds
      paneer, ghee and whey; peanut butter and coconut milk aren't dairy); and your vegetarian or no-egg menu;
    - *your goal*: today's calories after it against your target, and how much of the day it is in one go. Beside
      it, your weight's course over the last four weeks against keep, lose slowly or gain slowly;
    - *the guidelines*: protein, fibre, saturated fat, sodium and added sugar (Dietary Guidelines 2025-2030), and
      FDA's daily values (5% or less is low, 20% or more is high);
    - *your labs and conditions*: with an A1c in the prediabetes or diabetes range, carbohydrate with little fibre
      and more than a cup of white rice (Kerala matta or brown rice is suggested instead); with an LDL of 130 mg/dL
      or more, saturated fat at 6% of calories (the American Heart Association's advice); with blood pressure of
      130/80 or more, sodium at 1,500 mg.
    "What this rests on" shows each source. It is worked out by code from the food's numbers and your plan; no AI
    model sees any of it, and it isn't medical advice. Meals added from a lookup or the menu now keep their
    saturated fat and sugar too, so the day's totals count them.
- **Scan food** (the button over the Health screen, in the app): point the camera at a food pack's barcode. It is
  read on the phone by Google's ML Kit barcode scanner, bundled in the app, which works offline; no picture is kept
  or sent. Only the number goes to your server, which looks it up (the number only) in USDA's branded foods (with
  your USDA key) and Open Food Facts, and the Health page opens Plan → Food with the matches and the pack's label
  serving, ready to add. Short UPC-E codes on small US packs are written out to their full UPC-A first. No camera?
  Type the numbers under the barcode. The app asks for camera access the first time.
  - **Dish** (the second mode on the same screen): point the camera at a plate and Buddy suggests the three
    likeliest dishes, a few times a second; tap the right one and Plan → Food opens with USDA's matches for it.
    The suggestions come from Google's food classifier (the AIY Vision Classifier Food V1, Apache 2.0, about
    2,000 dishes), which runs on the phone through ML Kit and works offline. No picture is kept or sent: only the
    dish's name you tap goes to your server, and from there to USDA as a food lookup. It knows idli, sambar, upma,
    puttu, idiyappam, biryani, palak paneer, chana masala, rajma, chicken curry, fish fry, Greek salad, hummus,
    falafel, tabbouleh, pathiri, kottu and many more, but not dosa, appam, avial, thoran, rasam, pongal, uttapam,
    chapati, naan, grilled chicken, oatmeal or quinoa. **Teach it those:** type the dish's name, point the camera
    at it and tap **Teach**; turn the plate or step to one side and tap it again (3 to 5 views work best). Each view
    is kept as 1,024 numbers that describe what the camera saw, from Google's MobileNet-V3 image embedder (Apache
    2.0) running on the phone; never the picture. Next time, a dish you taught is suggested first ("one you
    taught"), and it works for your own home cooking too. They stay on the phone, in a folder Android doesn't back
    up, and nothing is sent anywhere; "Dishes you taught" lists them, and Forget removes one. A suggestion is a
    guess, so the meal is only added when you pick it and its portion.
- **Learn → Research:** new studies on the topics you choose, strongest evidence first, from PubMed (the US
  National Library of Medicine). Ready-made topics: longevity research (the journals Nature Aging, Aging Cell and
  GeroScience, and work from the National Institute on Aging, the Buck Institute and AFAR), South Asian heart and
  metabolic health, prediabetes, sleep, exercise and ageing, and supplements; and up to five of your own, in plain
  words. By default only guidelines, meta-analyses, systematic reviews and randomised trials, without studies done
  only in animals (both can be switched off). Buddy looks once a week, on Sunday night, over the last 30 days (90
  the first time), or when you press Refresh (at most once an hour). Each paper shows its journal, date, first
  author and evidence type, guidelines first, then reviews, then trials; **Abstract** fetches its abstract. A
  page, not a notification. No AI model reads or summarises any of it; only your topic words go to PubMed. Papers
  are listed 180 days, and every paper is also kept in the **paper library**. Research isn't advice: one study
  rarely changes what you should do.
  - **Paper library** (at the foot of Learn → Research): every research paper Buddy finds, from every source, kept
    once each until you delete it: PubMed (this digest and the freshness watch), arXiv (the research desk, trends,
    the tech radar, Buddy Intelligence), Europe PMC and OpenAlex (Buddy Intelligence). Each keeps its details, its
    abstract (the digest's new papers' abstracts are fetched with them, in one PubMed call) and which feature and
    topic found it. Search by words and source; Delete removes one. Public records; nothing is sent anywhere from
    it.
- **Learn → Supplements:** look up a product in the NIH Office of Dietary Supplements' Label Database (no key):
  products sold now first, then ones no longer sold. Open a label to see each ingredient per serving, its share of
  the daily value, and, for the nutrients with an adult upper limit (vitamin D, C, E and B6, niacin, calcium,
  iron, zinc, magnesium, selenium, iodine, copper, manganese, choline), its share of that limit at the label's
  most servings a day, warned when over 100%. Each nutrient links to ODS's fact sheet. It is what the label says,
  not a test of the product, and food adds to the limits.
- **Learn → Medicines:** a medicine's FDA label (what it is for, warnings, interactions, side effects) and recent
  recalls, from openFDA. Ask your pharmacist or doctor about your own medicines.
- These lookups send only the words you type (or the database's own label number), nothing about you, and keep a
  day's cache of the answers. No AI model is involved.
- **Plan → Menu (your daily diet):** targets worked out by code, and a day's menu built from the food you eat.
  - **Targets**, each with where it comes from: calories from what your devices estimate you burn (7 or more of
    the last 14 days), or else the Mifflin-St Jeor equation from your weight, height, age and sex times an activity
    factor from your steps, then your weight goal (keep; lose slowly, 500 less a day but never under 1,500 for men
    or 1,200 for women; gain slowly, 300 more). Protein 1.2 to 1.6 g per kg (Dietary Guidelines for Americans
    2025-2030). Fibre 14 g per 1,000 calories (the National Academies; the 2025-2030 guidelines give no number).
    Sodium under 2,300 mg, saturated fat at most 10% of calories, added sugar none (at most 10 g in a meal), all
    from the 2025-2030 guidelines. A calorie or protein number you typed in your plan wins. With an A1c in the
    prediabetes range, or South Asian ancestry, a note on carbs: whole grains over refined, brown rice over white,
    rice at the portion shown, protein and vegetables at every meal.
  - **The menu follows where you are.** Kerala food is hard to find in the US, so there the menu uses American
    (grilled chicken, salads) and Mediterranean dishes; in India, Kerala home food and other Indian dishes
    (biryani, palak paneer, dal, chana). Each place's list is yours to change. Where you are comes from your
    phone's last location (only its country is used: India, or the US list anywhere else), or you set it.
  - **The menu**: breakfast, lunch, dinner and a snack from meal templates in those cuisines, with plant foods
    always and eggs and fish, chicken or meat if you eat them (leave both off for vegetarian), rotated by date;
    every place and diet gets all four meals. Every dish is a USDA survey food (FNDDS 2024, `docs/data/dietfoods_build.py`) with
    USDA's own numbers and household portions, named as USDA names it; avial, thoran, appam, puttu and chapati have
    no USDA entry, so they aren't in it. Each meal's staple (rice, idli, dosa, quinoa, oatmeal, bread) is scaled to
    the meal's share of the day; brown rice replaces white with prediabetes-range numbers; protein is topped up at
    the snack when the day falls short. The day is checked against calories, protein, fibre, sodium and saturated
    fat. USDA's recipes are salted as people usually cook, so sodium often comes out high: the menu says so.
  - **Another** shows the next option for a meal; **I ate this** adds the meal to today's meals with all its
    numbers (calories, protein, carbs, fat, fibre, sodium). Foods added from a lookup keep them too; Plan → Food
    shows the day's fibre and sodium when meals carry them.
  - A suggestion from code, not medical advice; home recipes differ from USDA's. A registered dietitian can tailor
    it (often covered: Labs → Checkups). No AI model is given any of it.
- **Plan → Goals & habits:** goals, up to eight habits (tracked from the ring or phone with a target, or ticked off
  by you), and food targets. This form replaces the set-up conversation in section 2, and Plan opens on it until
  there is a plan. "Forget my health plan" (pressed twice) deletes the plan, the habit log and the meals; the
  readings stay until you delete them.
- **Trends → Patterns (your personal model):** (it also learns from blood pressure, cardio minutes, strength
  sessions and the meals' sodium, with the next morning's blood pressure as one of the outcomes) what, in your own days, tends to come before a better or worse next
  morning. Learned on your server by plain code once a week (Sunday night) or when you press Learn now, from the
  readings Buddy keeps: the day's steps, active minutes, workouts, WHOOP strain, time under stress, your journal's
  mood, stress and energy, and each habit you tick off yourself, that night's bedtime, and the next morning's
  readiness or recovery, sleep, HRV and resting heart rate.
  - **Patterns** compare the days above a factor's middle value with the days below (or a habit done with not
    ticked). One is shown only with at least 10 days on each side, a difference of at least a quarter of the
    measure's usual spread, and the same direction in at least 9 of 10 random redraws of the days.
  - **A model** per next-morning measure, once there are 60 days: a small linear model learned on your older days
    and tested on the newest 30% it never saw. It counts only if it beats always guessing your average by 5%.
  - Patterns, not causes, and not medical advice. No AI model is given any of it. The last 12 learnings are kept
    (no daily values in them); "Forget what was learned" deletes them.
- **Labs → Add a report:** add a lab report's PDF (Quest, Labcorp, or one downloaded from your portal, such as MyChart). The PDF's
  text is taken out in the sandbox and code picks out the tests it knows (about 45 common blood tests: lipids,
  glucose and A1c, thyroid, vitamins, iron, kidney and liver, blood count, electrolytes, hormones, PSA): each
  result, the lab's flag, its reference range and unit. No AI model reads the report. You then see every result
  beside the report's own line, untick anything wrong, correct a number or unit, set the date the sample was taken,
  and keep the rest; nothing is kept before that. Kept results show under **Labs → Results**, by test: the latest, below, within or above the
  lab's own range, and the earlier ones with which way it moved. The report is kept (encrypted) so you can open it
  again, until you delete it. Photos and scans can't be read: download the lab's own PDF.
  - **The report goes into your vault** (the library's encrypted store, covered by the backup). It is marked a
    health document: Buddy's library tool never lists, opens or unlocks it for an AI model, and none of its text
    is taken into the library, so it can't be searched or summarised. You open it from Labs → Results → Reports (and see it
    in the Library screen). Deleting the report deletes the vault copy too.
  - **When a report prints no range** (and no flag) for a test, Buddy fills in a typical adult range, marked
    "typical": guideline cut-offs where there is one (fasting glucose and A1c: ADA; cholesterol: NCEP; hs-CRP:
    AHA/CDC), otherwise the usual adult range MedlinePlus gives. Only when the report's unit is the table's unit,
    and for tests whose range differs for men and women (ferritin, creatinine, hemoglobin, hematocrit, red cells,
    HDL, PSA) only when your sex is known from the Oura profile. No typical range is guessed for tests where labs
    disagree too much (TSH, free T4, ALT, insulin, cortisol, testosterone). The lab's printed range always wins.

- **Labs → Checkups:** which screenings apply to you, by fixed rules, from a few answers you give once: birth
  year, sex at birth, ancestry (pick all that apply: Indian, with Kerala, other South India and elsewhere in India
  apart; Pakistani, Bangladeshi, Sri Lankan, Nepali; Chinese, Japanese, Korean; Filipino, Vietnamese; and others),
  whether you were born in or lived over a year in a country where hepatitis B or TB is common, smoking, family
  history, conditions you've been told you have, height, waist, and the kind of health plan you have (only its kind;
  no insurer, member number or money). Age, sex and height are offered from the Oura profile for you to check.
  - **The screenings** are the US Preventive Services Task Force's A and B recommendations for adults (and its C for
    prostate, shown as a choice to make with your doctor): blood pressure, diabetes and prediabetes, cholesterol,
    colorectal, lung, breast and cervical cancer, abdominal aortic aneurysm, BRCA risk, osteoporosis, hepatitis B
    and C, HIV, latent TB, the questions asked at a checkup (depression, anxiety, alcohol, drugs, smoking), help to
    stop smoking, diet and activity coaching, and falls. Each says why it applies to you, how often, the
    guideline's link and grade, and a coverage note, grouped as **Due**, **To talk over with your doctor** and
    **Up to date**.
  - **Ancestry changes the rules where the guidelines do:** the task force screens Asian Americans for diabetes from
    a BMI of 23 instead of 25 (marked "often $0; ask your plan first", since that comes from its notes, not its
    grade); the ADA tests every adult from 35 and counts Asian, Black, Hispanic, Native American and Pacific
    Islander ancestry as a risk factor; the 2026 ACC/AHA dyslipidemia guideline counts higher-risk ancestry
    (South Asian, Filipino) as a risk enhancer for starting a statin, and recommends measuring Lp(a) once in every
    adult; and being born in most of Asia (India and China included) brings in hepatitis
    B and TB screening. With Kerala picked, the diabetes line adds that about 1 in 4 adults there had diabetes in
    India's ICMR-INDIAB study (Lancet Diabetes & Endocrinology, 2023), against 11.4% across India.
  - **When it was last done:** a matching lab result fills it in (A1c or glucose, cholesterol, PSA); otherwise you
    note the month under Details (and which test, for colorectal and cervical, since that sets the next one).
  - **Lp(a), glucose sensor, DNA: worth it?** Lipoprotein(a) once, as the 2026 ACC/AHA dyslipidemia guideline
    recommends for every adult (more often high with South Asian ancestry; a result of 50 mg/dL or 125 nmol/L and
    over is a risk enhancer). A glucose sensor (CGM): worth asking about only with an A1c or glucose in the
    diabetes range; optional and paid by you in the prediabetes range, where the proven step is a lifestyle
    programme and a yearly A1c; not needed when normal; an A1c comes first. Genetic testing: worth asking about with
    breast or ovarian cancer in the family (or Ashkenazi ancestry), colon or uterine cancer before 50 in the family,
    or an LDL of 190 or more; otherwise not needed, and Buddy doesn't take DNA files.
  - **Compared with others:** how many in 100 US adults of your sex and age have a lower BMI, waist, blood
    pressure, A1c, cholesterol or glucose than yours, among those of Asian ancestry and among everyone, from the
    CDC's NHANES survey (2017–March 2020 and 2021–2023, weighted). NHANES can't split Indian from Chinese
    ancestry. With Asian ancestry it also gives the WHO's Asian BMI action points (23 and 27.5) and the
    International Diabetes Federation's waist line (90 cm for men, 80 cm for women).
  - Coverage notes are general: under the ACA most non-grandfathered private plans cover A and B screenings
    in-network at $0; Medicare and Medicaid have their own lists. Ask your plan before you book. Buddy never orders
    a test, books a visit or contacts a doctor, and this is not medical advice. Your answers stay on your server,
    no AI model is given them, and "Delete my answers" (asked twice) deletes them and the dates you noted.

- **Is Buddy up to date?** (the top of Sources). Buddy's health rules are fixed in code so they stay exact; they
  don't change by themselves. This card says when one may be out of date, and whether a newer Buddy is ready:
  - **Screenings** (the task force): once you have its free data key, Buddy fetches the task force's whole data set
    once a week (nothing about you is sent) and compares each recommendation's grade and ages with the week
    before; topics newer than the date Buddy's rules were checked are flagged too. Its own words are kept and shown
    verbatim under Details on Checkups, as its terms require. Request the key from AHRQ (the Prevention TaskForce
    API request form, or email uspstfpda@ahrq.gov with the subject "Requesting a Prevention TaskForce API Key"),
    then `sudo bash /opt/buddy/deploy/set.sh BUDDY_USPSTF_KEY`, and `BUDDY_USPSTF_URL` if AHRQ's reply gives an
    address other than epssdata.ahrq.gov. Until then Buddy watches PubMed for new task force statements (JAMA).
  - **Diabetes rules** (a newer ADA Standards of Care) and **cholesterol and heart-risk rules** (a guideline newer
    than the 2026 ACC/AHA dyslipidemia guideline): PubMed. **Menu dish numbers**: USDA's API, for a newer
    survey-food release (your USDA key). **Compared with others**: CDC's files, for the next NHANES cycle. **Diet
    targets**: the next Dietary Guidelines are due in 2030.
  - A screening a newer task force recommendation touches moves to **Under review** on Labs → Checkups, with what
    was published, until the rules are updated (ask for it; the change comes with tests) and deployed.
  - **Buddy itself**: once a day the server compares its code with the repository's branch (read only: nothing is
    fetched or changed) and, when a newer version is ready, shows the deploy command.
  - Weekly on Sunday night, daily for Buddy's own version, or **Check now** (at most hourly). No notifications.
- **Sources:** where your health data comes from. For each source (the phone's Health Connect, the Oura Ring):
  whether it works, when it last synced and what kind of sync that was (the first one, an update, sent by the
  phone, or one you asked for), how many values came and how many were new, a failed sync with its error, the
  last syncs, what it sends, how much is held and for how long, and its terms. Each measure says how it is
  obtained: **measured by a device** (heart rate, HRV, steps, temperature, blood oxygen), **estimated by the
  maker's software** (sleep and its stages, calories, scores), **entered by you**, or **a lab result**. A ring is
  not a lab. Where a standard code fits exactly, it is shown (LOINC 41950-7 steps, 40443-4 resting heart rate,
  41979-6 calories burned); Oura's HRV is RMSSD, which LOINC's HRV code (SDNN) is not, so it has none. Buttons:
  **Sync now** (Oura), **Delete its data** and **Disconnect** (Oura), each delete or disconnect asked twice. The
  phone's data is turned off on the phone. When Oura stops accepting Buddy's sign-in, the source and the
  Connections screen say **Sign in again**. At the top, when the ring and the phone were last read, with **Read the
  ring now**.

What it does not do: it gives no advice beyond those fixed rules, and it is not medical advice.

## Garmin, Fitbit, Pixel Watch, Samsung and other devices (through Health Connect)

These write to Google Health Connect on your phone, and the Buddy app reads Health Connect, so no separate
connection is needed: Garmin Connect (since mid-2025), the Fitbit app (also for the Pixel Watch), Samsung Health, and
scales, blood pressure cuffs and glucose meters whose apps write there.
1. In each device's app, turn on syncing to **Health Connect** (Garmin Connect: Settings → Health Connect; Fitbit:
   Settings → Health Connect; Samsung Health: Settings → Health Connect).
2. In the Buddy app: Connections → Health → Phone health data → allow the types you want: steps, sleep, heart rate,
   resting heart rate, exercise and calories as before, and now HRV, blood oxygen, weight, body fat, blood pressure,
   blood glucose, breathing rate and VO2 max. Each type is your choice.
3. Health → Sources → Phone health data shows which apps wrote to Health Connect lately ("Garmin Connect, Fitbit").
Each day Buddy gets the day's total or average (for weight, body fat, blood pressure and VO2 max, the day's last
reading), never the minute-by-minute readings. What each app shares with Health Connect is that app's choice
(Garmin shares a set of about 15 kinds of data, for example). Google's Health API (the Fitbit cloud's successor)
is not used: it needs Google's review of the app, and Health Connect gives the same data from the phone.

## WHOOP

Set up once on the server, like the Oura Ring: create an app at developer.whoop.com, then
`sudo bash /opt/buddy/deploy/whoop.sh` (it shows the return address to put on the app and asks for the client ID
and secret). Then in the Buddy app: Connections → Health → WHOOP → Connect, and allow access. Buddy reads a year of
history the first time, then the last 14 days every three hours (WHOOP rescores recent days): each day's recovery
(score, resting heart rate, HRV, blood oxygen, skin temperature), strain, calories and heart rate, the main sleep
(deep, REM, light, efficiency, performance, breathing rate) and workouts. The health plan reads WHOOP when there is
no Oura reading for a day. Built from WHOOP's published API and tested against a made-up WHOOP, not a real band.

## Lab results from MyChart (patient portals)

Health → Labs → **Portals** (MyChart): find your health system in Epic's public list, tap **Sign in**, sign
in at your health system, and allow Buddy to read your results. Buddy reads your lab results (read only) right away
and then once a day, and they join your results beside uploaded reports, each with the health system's own test
code (LOINC), unit, reference range and flag. Results with no number (a "negative", a comment) are not kept. Tests
Buddy has no name for are listed under their code and are never in the lines Claude may get. Nothing goes to an AI
model. Disconnect on the same card (Buddy forgets its sign-in; the results stay until you delete them on Sources);
also remove Buddy from MyChart's linked apps to end the access at the health system.

**Set up once (about 15 minutes; Epic decides when it works at your health system):**
1. Create a free account at **fhir.epic.com** ("Epic on FHIR") and choose **Build Apps → Create**.
2. Application audience: **Patients**. Name it (for example "Buddy, personal"). Incoming APIs: the R4 **Patient.Read**,
   **Observation.Read (Labs)** and **DiagnosticReport.Read** APIs. SMART on FHIR version R4; **not** a confidential
   client (Buddy uses PKCE); ask for **refresh tokens** if offered, so Buddy can read new results without you signing
   in each time.
3. Redirect URI: your server's own return page, shown under Labs → Portals, `https://<your tailnet name>/connect/mychart/linked`.
4. Save, mark it ready for production, and copy the **production client ID** (the non-production one works only with
   Epic's test site). On the server: `sudo bash /opt/buddy/deploy/set.sh BUDDY_EPIC_CLIENT_ID` and paste it.
5. Epic shares a new patient app's client ID with health systems on its own schedule (it can take a day or more), and
   some health systems approve apps themselves. Until it reaches yours, the sign-in page at your health system says
   the app isn't recognised: try again later. Without a refresh token your health system asks you to sign in again
   after an hour; Buddy then shows **Sign in again** on Sources and the Connections screen.

Untested against a real health system: tested against a made-up one that follows the SMART App Launch and FHIR R4
rules. The first real sign-in is the true test.

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

- Oura, WHOOP and Health Connect data, the meals, the habit log and the mood journal: kept on your server until
  you delete them (Health → Sources → Delete its data), for future reference and so Buddy's own model on your server
  has history to learn from. To set a limit instead: `sudo bash /opt/buddy/deploy/set.sh BUDDY_HEALTH_KEEP_DAYS`
  (30 to 3650 days; 0 for none). Oura's first sync reads the last year. No AI model is given them,
  Claude included. If you switch on health ranges on the server, Claude, and only Claude, is told how a measure compares
  with your usual ("sleep: below usual"), never the value (`docs/PRIVACY-GATEWAY.md`).
- The plan stays until you change it; the habit log and meals as long as the readings (until you delete them).
- Lab reports and results: until you delete the report (Health → Labs → Results → Reports → Delete); results from MyChart until you
  delete them (Health → Sources → MyChart → Delete its data). No AI model reads a report or is
  given a result. **Lab ranges** are their own switch, off unless you turn it on, on the server:
  `sudo bash /opt/buddy/deploy/set.sh BUDDY_LAB_RANGES`, then type `on`. When on, Claude and only Claude may be
  given one line a test, "LDL cholesterol: above the lab's range; up since the last test", never the value, unit,
  date or lab. It does tell Claude which tests you have had and whether each is in range.
- **"Forget my health plan"** deletes the plan, habit log and meals.
- To stop Oura syncing: Health → Sources → Oura Ring → **Disconnect** (Buddy forgets its sign-in and deletes
  what the ring gave it), and remove Buddy in your Oura account (Connected apps). To turn Oura off for the whole
  server, clear the `BUDDY_OURA_*` settings with `set.sh` (press Enter).
- **Delete its data** (Health → Sources) deletes one source's readings, their health records and its sync
  history, and leaves the other source's alone.
- Each reading is also kept once in a common form with its source (`health_records`: the value in a standard
  unit, the source's own value and unit, how it was obtained, the sync it came in), for as long as its source.
  This is the form lab results will be read in. The sync history keeps counts, dates and errors, never a value:
  the last 50 syncs per source, a year at most.
