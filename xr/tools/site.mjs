#!/usr/bin/env node
// Builds the things made from the tutorial app's source, so what a reader sees is the code that is tested:
//   studio/templates.js       the starting points offered in the Studio
//   <out>/buddy-xr-tutorial.html   the tutorial page for the website (only when an output folder is given)
//
//   node tools/site.mjs                 rewrite studio/templates.js
//   node tools/site.mjs <website docs>  also write the tutorial page there

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { REFERENCE } from "../studio/reference.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BLOCKS = ["Alert", "Ask", "Image", "List", "Progress", "Screen", "Stat", "Text", "defineApp"];
const read = (path) => readFileSync(join(root, path), "utf8");
const importLine = (code) => `import { ${BLOCKS.filter((b) => new RegExp(`\\b${b}\\(`).test(code)).join(", ")} } from "buddy-xr";`;

/** The tutorial's steps as stand-alone apps: [{ step, code }]. A step that uses a list defined in an earlier
 *  step gets that line too, so each one runs by itself. */
export function steps() {
  const source = read("apps/tutorial/steps.js");
  const regions = [...source.matchAll(/\/\/ --- step (\d+)\n([\s\S]*?)\/\/ --- end/g)].map((m) => ({ step: Number(m[1]), body: m[2].trim() }));
  const shared = {};                                       // NAME -> its one-line definition, from the step that introduced it
  return regions.map(({ step, body }) => {
    for (const m of body.matchAll(/^const ([A-Z_]+) = .*;$/gm)) shared[m[1]] = m[0];
    const borrowed = Object.entries(shared).filter(([name, line]) => new RegExp(`\\b${name}\\b`).test(body) && !body.includes(line)).map(([, line]) => line);
    const app = body.replace(/export const step\d+ = \(\) => defineApp\(/, "export default defineApp(");
    if (!app.includes("export default defineApp(")) throw new Error(`Step ${step} does not define an app.`);
    const code = [importLine(app), "", ...borrowed, ...(borrowed.length ? [""] : []), app, ""].join("\n");
    return { step, code };
  });
}

const BLANK = `import { Ask, List, Screen, Text, defineApp } from "buddy-xr";

export default defineApp({
  name: "My app",
  state: { count: 0 },
  view(state) {
    return Screen({ title: "My app" },
      Text(\`You have chosen \${state.count} times.\`),
      List(["Choose me", "Or me"], { onSelect: (item, index, ctx) => ctx.set({ count: state.count + 1 }) }));
  },
});
`;

/** What the Studio offers to start from. */
export function templates() {
  const names = ["One screen", "A list to choose from", "A question", "Something private", "News from outside, and voice"];
  const visit = read("apps/visit/app.js").replace(/^\/\/ It is an example.*\n/m, "").replace('"../../src/index.js"', '"buddy-xr"')
    .replace(/\nexport const demo = .*\n/, "\nexport default createVisit();\n");
  return [{ id: "blank", name: "Start here: a small app", code: BLANK, events: "" },
    ...steps().map((s, i) => ({ id: "step" + s.step, name: `Tutorial ${s.step}: ${names[i]}`, code: s.code,
      events: s.step === 5 ? '{ "type": "parcel", "address": "9 Quay Street" }' : "" })),
    { id: "visit", name: "Example: a technician's site visit", code: visit, events: '{ "text": "The part has arrived at the van." }' }];
}

export const templatesFile = () => "// Made by tools/site.mjs from the tutorial app and the example app. Do not edit by hand.\n"
  + `export const TEMPLATES = ${JSON.stringify(templates(), null, 1)};\n`;

/** CLAUDE.md: what Claude Code reads when it works in this folder. */
export const claudeFile = () => `<!-- Made by tools/site.mjs from studio/reference.js. Do not edit by hand. -->
${REFERENCE.replaceAll('"buddy-xr"', '"../../src/index.js"').replace("# Writing a Buddy XR app", "# Buddy XR: instructions for Claude Code")}
## Working in this folder

- In this repository an app imports the framework by path, as above, from \`apps/<name>/app.js\`. Outside it, the
  import is \`"buddy-xr"\`.
- Start an app with \`node bin/buddyxr.js new <name>\`: it creates the app and a test.
- After every change run \`node --test\` and \`node bin/buddyxr.js check <app.js>\`. Both must pass before you say
  the app works. The check lists every kind of glasses; an error there is a real failure.
- Test with \`memoryHost(CLASSES.text)\` and \`memoryHost(CLASSES.audio)\`: assert the exact lines (\`host.text\`) and
  the exact sentences (\`host.spoken\`). See \`test/tutorial.test.js\` for the pattern.
- To look at an app: \`node bin/buddyxr.js sim\`, then open the Studio at /studio/.
- Nothing here has run on real glasses. Do not claim that an app works on a device; say it passes the tests and
  the check.
`;

// --- the tutorial page ----------------------------------------------------------------------------------------
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

const PARTS = [
  { title: "One screen", lead: "An app is a name and a function that returns a screen. A screen has a title and blocks inside it. Here there is one block: a sentence.",
    try: "Change the glasses to Audio glasses. The same app is now read out. You wrote nothing for that.",
    notes: ["<code>defineApp</code> makes the app. <code>view</code> returns what the wearer should get.", "<code>Screen</code> and <code>Text</code> are blocks. They describe; they do not draw."] },
  { title: "A list to choose from", lead: "Give the app something to remember (its state), and a list the wearer can pick from. Choosing changes the state, and the screen is made again from it.",
    try: "Press Tap to move and Double tap to choose. On Audio glasses, say “next” and “select”. Each choice is read on its own.",
    notes: ["<code>state</code> is where the app starts.", "<code>ctx.set(...)</code> changes the state. You never redraw anything yourself.", "<code>onSelect</code> makes a list something to choose from. Without it, the list is only shown."] },
  { title: "A question", lead: "Choosing a parcel now opens it and asks a yes-or-no question. The app decides which screen to return from its state.",
    try: "Choose a parcel, then say “yes” or “no”, or move to an answer and Double tap. Long press goes back.",
    notes: ["<code>Ask</code> is a question with two answers. The wearer can say them or choose them.", "<code>onBack</code> is what a long press, or saying “back”, does."] },
  { title: "Something private", lead: "Some parcels have a gate code. Mark it personal. It is shown in the lens, which only the wearer sees, and it is not read out where people nearby would hear it.",
    try: "Open 12 Mill Lane on Text-display glasses: the code is there. Now on Audio glasses: the wearer is told to check their phone. Tick “Read personal details aloud” to see what the wearer can choose.",
    notes: ["<code>privacy: \"personal\"</code> is all the app does. The rule is applied underneath, and an app cannot switch it off.", "<code>Progress</code> is a bar on a colour display, a percentage on a text display and a sentence in the ear."] },
  { title: "News from outside, and the wearer's words", lead: "The depot adds a parcel while the round is under way, and the driver can say “skip” to put a parcel at the end. An alert is always spoken where there is a speaker.",
    try: "Press “Depot: a new parcel” under Meanwhile. Then open a parcel and say “skip”.",
    notes: ["<code>onEvent</code> receives whatever your own system sends.", "<code>onVoice</code> receives words that were not a command.", "<code>needs: [\"mic\"]</code> says the app cannot work without a microphone, so the check can say where it would fail."] },
];

export function tutorialPage() {
  const all = steps();
  const workflow = (name) => esc(read(`templates/github/${name}`).trim());
  const section = (s, i) => `
<h2 id="step${s.step}">Step ${s.step}. ${PARTS[i].title}</h2>
<p>${PARTS[i].lead}</p>
<pre><code>${esc(s.code.trim())}</code></pre>
<ul>${PARTS[i].notes.map((n) => `<li>${n}</li>`).join("")}</ul>
<div class="sim" id="sim${s.step}"></div>
<p class="try"><strong>Try it.</strong> ${PARTS[i].try} <a href="xr/studio/#step${s.step}">Open this step in the Studio</a> to change the code.</p>`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Buddy XR tutorial: build a glasses app, start to finish · Buddy Labs</title>
<link rel="icon" href="../favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="../styles.css">
<style>
.pill { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: var(--brand-soft); color: var(--brand); }
.pill.warn { background: #f9eeda; color: #8a5a12; }
.sim { margin: 16px 0 8px; }
.try { background: var(--brand-soft); border-radius: 10px; padding: 10px 14px; }
</style>
</head>
<body>
<header class="site"><div class="wrap">
  <a class="logo" href="../index.html"><span class="mark" aria-hidden="true">B</span><span>Buddy Labs</span></a>
  <nav class="site" aria-label="Main"><a href="../xr.html">Buddy XR</a><a href="buddy-xr-tutorial.html" aria-current="page">Tutorial</a><a href="xr/studio/">Studio</a><a href="buddy-xr.html">Developer guide</a></nav>
</div></header>
<main><div class="wrap docs">
<aside class="docnav"><strong>Tutorial</strong>
<a href="#start">Before you start</a>${all.map((s, i) => `<a href="#step${s.step}">${s.step}. ${PARTS[i].title}</a>`).join("")}
<a href="#test">6. Test it</a><a href="#check">7. Check every kind of glasses</a><a href="#agent">8. Give it to an AI agent</a><a href="#glasses">9. Put it on real glasses</a><a href="#ci">10. Claude Code and automatic checks</a><a href="#next">Where next</a>
<a href="xr/studio/">Studio</a><a href="buddy-xr.html">Developer guide</a></aside><article class="doc">

<h1>Build a glasses app, start to finish</h1>
<p class="lead">In ten steps you build a delivery driver's app that works on glasses with a display and glasses with
only a speaker, keeps a gate code private, takes news from a depot, and can be driven by an AI agent. Every step runs
on this page.</p>

<h2 id="start">Before you start</h2>
<ul>
<li><strong>You need nothing installed.</strong> Each step has the code and, under it, the app running in a simulator.
The <a href="xr/studio/">Studio</a> lets you change the code in your browser.</li>
<li><strong>You need to know a little JavaScript.</strong> An app is one file.</li>
<li><strong>On your own machine</strong> the toolkit needs Node 20 or later and has no other dependencies. It is given
to early-access partners: <a href="mailto:hello@buddylabs.io?subject=Buddy%20XR%20toolkit">ask for it</a>.</li>
</ul>
<p><span class="pill warn">Know this first</span> The simulator shows what the framework sends to glasses. Nothing in
this tutorial has run on a real pair yet; step 9 says exactly what is and is not ready.</p>
${all.map(section).join("\n")}

<h2 id="test">Step 6. Test it</h2>
<p>An app can be tested without glasses and without a browser. A memory host keeps every line shown and every sentence
spoken, so a test can check the exact words. This is the toolkit's own test for step 4:</p>
<pre><code>import test from "node:test";
import assert from "node:assert/strict";
import { CLASSES, memoryHost, runApp } from "buddy-xr";
import app from "./app.js";

test("the gate code is shown in the lens and not read out", () =&gt; {
  const lens = memoryHost(CLASSES.text);
  runApp(app, lens);
  lens.input({ type: "double" });                       // choose the first parcel
  assert.equal(lens.text, "12 Mill Lane\\nGate code 2580\\nDelivered?\\n› Yes\\n  No");

  const ear = memoryHost(CLASSES.audio);
  runApp(app, ear);
  ear.voice("select");
  assert.equal(ear.spoken.at(-1),
    "12 Mill Lane. You have a private update. Check your phone. Delivered? Say yes or no.");
});</code></pre>
<p>Run it with <code>node --test</code>. <code>buddyxr new my-app</code> creates an app with a test like this already in place.</p>

<h2 id="check">Step 7. Check every kind of glasses</h2>
<p>One command runs the app on every kind of glasses the framework knows and says where it would fail, or lose
something. The Studio shows the same result under the simulator.</p>
<pre><code>$ node bin/buddyxr.js check app.js
Delivery round

works  Audio glasses
works  Text-display glasses
works  Colour-display glasses
...
FAILS  (glasses with a microphone only)
         error: No display and no speaker: the app cannot tell the wearer anything.</code></pre>
<p>It reports three kinds of thing: an <strong>error</strong> (the app does not work there, such as a missing
microphone the app said it needs), a <strong>warning</strong> (a picture with no words to stand in for it), and
<strong>information</strong> (private lines held back, or a screen that takes several pages on a small display).</p>

<h2 id="agent">Step 8. Give it to an AI agent</h2>
<p>An agent does not need an app. It gets four tools: describe the glasses, show something, ask a yes-or-no question,
and offer a choice. Each answer says what actually reached the wearer.</p>
<pre><code>import { agentSession } from "buddy-xr";

const session = agentSession(host, { name: "Depot", onSay: (words) =&gt; sendToAgent(words) });

await session.call("glasses_show", {
  title: "Next stop", lines: ["12 Mill Lane"], alert: "Road closed on Mill Lane",
});

const gate = await session.call("glasses_show", {
  title: "12 Mill Lane", lines: ["Gate code 2580"], privacy: "personal",
});
// on audio glasses: { shown: false, spoken: true, withheld: 1 }
// the agent knows the code was not read out, and can say "it is on your phone"

const { answer } = await session.call("glasses_ask", { question: "Delivered?" });   // "yes" or "no"</code></pre>
<p><code>session.tools</code> holds the four tools in the shape tool-calling models expect (a name, a description and
an input schema), so they can be handed to a model as they are. <span class="pill warn">Not yet</span> a server that
offers them to an agent running somewhere else: today the agent and the session run in the same program.</p>

<h2 id="glasses">Step 9. Put it on real glasses</h2>
<p>The app does not change. What changes is the <em>host</em>: the one part that knows a device. Two hosts for real
glasses are written. <span class="pill warn">Neither has run on a pair yet.</span></p>
<table>
<tr><th>Route</th><th>What you need</th><th>What to know</th></tr>
<tr><td>Through an open-source glasses runtime</td><td>A developer account with that runtime, an app registered there, its key, and supported glasses (or its simulated pair)</td><td>One host reaches every pair the runtime supports. The wearer's speech arrives as words. Everything passes through the runtime's cloud, so personal lines are held back unless you switch that on.</td></tr>
<tr><td>Through one maker's plugin system</td><td>That maker's glasses and phone app, and its developer kit</td><td>The app runs as a web page inside the maker's phone app. Touch works; the wearer's words need a speech-to-text service you supply.</td></tr>
</table>
<p>With the runtime, your app becomes a small server:</p>
<pre><code>import { AppServer } from "@mentra/sdk";            // the runtime's own kit
import { mentraHost, runApp } from "buddy-xr";
import app from "./app.js";

class DeliveryRound extends AppServer {
  async onSession(session) {
    runApp(app, mentraHost(session));               // the same app as in the simulator
  }
}

new DeliveryRound({
  packageName: process.env.PACKAGE_NAME,            // from the runtime's developer console
  apiKey: process.env.MENTRAOS_API_KEY,             // keep it in the environment, never in the file
  port: 3000,
}).start();</code></pre>
<p>Publishing to a maker's store is a separate step that each maker controls: its own account, packaging and review.
The framework does not publish for you.</p>

<h2 id="ci">Step 10. Let Claude Code build it, and check every change automatically</h2>
<p>If your app lives in a GitHub repository, two workflow files do this for you. Both ship with the toolkit in
<code>templates/github/</code>.</p>
<p><strong>The check</strong> needs no AI and no key. On every push and pull request it runs the tests, then every app
on every kind of glasses. A change that breaks an app on any kind of glasses fails before it is merged.</p>
<pre><code>${workflow("buddy-xr-check.yml")}</code></pre>
<p><strong>Claude Code</strong> builds and changes apps when someone who can write to the repository mentions
<code>@claude</code> in an issue or pull request, for example: <em>“@claude build an app in xr/apps/picking: a warehouse
picking list. Show the next item and its shelf, let me say done, and ask before closing the order.”</em> It reads the
toolkit's <code>CLAUDE.md</code> (the blocks, the privacy rule, and “run the tests and the check before saying it
works”), writes the app with its test, and opens a pull request. The check above then runs on it.</p>
<pre><code>${workflow("buddy-xr-claude.yml")}</code></pre>
<ul>
<li><strong>It runs on your own Claude account, not ours.</strong> You add one secret to your repository: an API key,
billed by use, or a token for your Claude subscription. We never see it and nothing is billed through us.</li>
<li><strong>Keep the key out of files.</strong> It belongs in the repository's secrets only.</li>
<li><strong>Read what it wrote.</strong> The pull request is a proposal. The check tells you it runs on every kind of
glasses; it does not tell you it is the app you wanted.</li>
<li>The action's settings change over time: its own guide is the authority.</li>
</ul>
<p><span class="pill warn">Planned for subscribers</span> On the paid plans of the Buddy platform this becomes one
step: connect your repository and your own Claude account once, and Claude Code builds and updates your apps from
there, with the check run for you. It is not built yet. Until then, the two files above do the same by hand.</p>
<p>Without GitHub, the same works by hand: in the <a href="xr/studio/">Studio</a>, describe the app, press “Copy
instructions for Claude Code”, paste them into any AI assistant, and paste the code it returns into the editor.</p>

<h2 id="next">Where next</h2>
<ul>
<li><a href="xr/studio/">The Studio</a>: change any step, or start your own app, in the browser.</li>
<li><a href="buddy-xr.html">The developer guide</a>: every block, the privacy rule in full, and what is not built.</li>
<li><a href="../pricing.html">Pricing</a>: building is free; planned prices for what runs.</li>
<li><a href="mailto:hello@buddylabs.io?subject=Buddy%20XR%20early%20access">Ask for early access</a>.</li>
</ul>

</article></div></main>
<script type="module">
import { mountSimulator } from "./xr/simulator/sim.js";
import { STEPS } from "./xr/apps/tutorial/steps.js";
STEPS.forEach((make, i) => mountSimulator(document.getElementById("sim" + (i + 1)), {
  makeApp: make,
  profiles: ["text", "color", "audio"],
  phrases: i === 0 ? [] : i === 1 ? ["next", "select"] : i === 4 ? ["next", "select", "yes", "no", "skip", "back"] : ["next", "select", "yes", "no", "back"],
  events: i === 4 ? [{ label: "Depot: a new parcel", event: { type: "parcel", address: "9 Quay Street" } }] : [],
}));
</script>
</body>
</html>
`;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  writeFileSync(join(root, "studio/templates.js"), templatesFile());
  writeFileSync(join(root, "CLAUDE.md"), claudeFile());
  console.log("studio/templates.js\nCLAUDE.md");
  if (process.argv[2]) { const out = join(resolve(process.argv[2]), "buddy-xr-tutorial.html"); writeFileSync(out, tutorialPage()); console.log(out); }
}
