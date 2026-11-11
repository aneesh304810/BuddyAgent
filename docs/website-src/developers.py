# For developers and technical leads: how Buddy's code is organised and how to extend it safely. /docs/developers
# Run by apidocs.py (after architecture.py), so docs_page, ARCH_STYLE, CO and DOCS_UPDATED are defined. Module names
# are fine here; never a server address, port, token, key, password or file path on the server.

DEV_SIDE = """
<aside class="docnav"><strong>Developers</strong>
<a href="#stack">Stack</a><a href="#runtime">Runtime model</a><a href="#map">Code map</a><a href="#modules">Key modules</a>
<a href="#protocol">App protocol</a><a href="#tools">Tools</a><a href="#models">Model routing</a>
<a href="#connector">Adding a connector</a><a href="#background">Background work</a><a href="#datamodel">Data model</a>
<a href="#config">Settings</a><a href="#testing">Testing</a><a href="#ci">CI and release</a><a href="#debug">Debugging</a>
<a href="#checklist">Change checklist</a><a href="#lead">For tech leads</a>
<a href="architecture.html">Architecture</a><a href="index.html">API documentation</a></aside>"""

LAYERS_SVG = """
<figure class="dia" id="fig-layers"><svg viewBox="0 0 760 360" role="img" aria-labelledby="lyt">
<title id="lyt">Code map in layers: entry points, the conversation, tools and domains, shared services, storage.</title>
<rect x="10" y="10" width="740" height="62" rx="12" class="box"/><text x="24" y="32" class="w">ENTRY POINTS</text>
<text x="24" y="54" class="s">server (HTTP API, live link /ws, admin, bank and connect pages, call webhooks)  ·  proactive loop  ·  deploy scripts</text>
<rect x="10" y="84" width="740" height="62" rx="12" class="srv"/><text x="24" y="106" class="w">CONVERSATION</text>
<text x="24" y="128" class="s">agent (prompt, tools, streaming, approvals) · router (which model) · free_models · privacy (masking) · thrift, budgets</text>
<rect x="10" y="158" width="740" height="92" rx="12" class="mod"/><text x="24" y="180" class="w">TOOLS AND DOMAINS</text>
<text x="24" y="202" class="s">money · plaid · cards · finance · markets · invest   |   health_data · oura · coach   |   travel · transit · flights · commute · maps · routine</text>
<text x="24" y="220" class="s">calls · contacts · scheduling   |   mailbrain · organiser · messaging · nest · weather · packages · trends · research   |   codedocs · selfcode</text>
<text x="24" y="238" class="s">Each domain exposes TOOL (a JSON schema) and run_tool(memory, args); the agent dispatches by name.</text>
<rect x="10" y="262" width="740" height="40" rx="12" class="ext"/><text x="24" y="287" class="s"><tspan class="w">SHARED SERVICES  </tspan>gateway (Buddy Connect) · connectors.hub · sandbox · usage (costs) · guard (access) · tools (time, https rules)</text>
<rect x="10" y="314" width="740" height="40" rx="12" class="sec"/><text x="24" y="339" class="s"><tspan class="w" fill="#b3261e">STORAGE  </tspan>memory.Memory over one SQLCipher database (db) · key-value settings · tables per domain created on first use</text>
</svg><figcaption>Figure D1. Dependencies point down: domains use shared services and storage, never the server or each
other's internals. A domain can be tested with a Memory on a temporary file and a fake network.</figcaption></figure>
"""

PROTOCOL_SVG = """
<figure class="dia" id="fig-protocol"><svg viewBox="0 0 760 300" role="img" aria-labelledby="prt">
<title id="prt">The app's live link: the app sends user, voice and location messages; the server answers with heard,
thinking, say, cards, approval and done, and sends nudges at any time.</title>
<rect x="40" y="14" width="160" height="36" rx="10" class="box"/><text x="120" y="37" class="t" text-anchor="middle">Buddy app</text>
<rect x="560" y="14" width="160" height="36" rx="10" class="mod"/><text x="640" y="37" class="t" text-anchor="middle">Server /ws</text>
<path class="line" d="M120,50 V290 M640,50 V290" stroke-dasharray="2 5"/>
<path class="flow" d="M120,72 H640" marker-end="url(#ah)"/><text x="380" y="67" class="s" text-anchor="middle">voice (audio) or user (text), location, mode</text>
<path class="flow" d="M640,98 H120" marker-end="url(#ah)"/><text x="380" y="93" class="s" text-anchor="middle">heard (what was understood)</text>
<path class="flow" d="M640,124 H120" marker-end="url(#ah)"/><text x="380" y="119" class="s" text-anchor="middle">thinking (which tool is running)</text>
<path class="flow" d="M640,150 H120" marker-end="url(#ah)"/><text x="380" y="145" class="s" text-anchor="middle">say (streamed text) and cards</text>
<path class="flow" d="M640,176 H120" marker-end="url(#ah)"/><text x="380" y="171" class="s" text-anchor="middle">approval (what Buddy wants to do: yes / no)</text>
<path class="flow" d="M120,202 H640" marker-end="url(#ah)"/><text x="380" y="197" class="s" text-anchor="middle">the owner's answer</text>
<path class="flow" d="M640,228 H120" marker-end="url(#ah)"/><text x="380" y="223" class="s" text-anchor="middle">done (turn finished, cost)</text>
<path class="flow pub" d="M640,262 H120" marker-end="url(#ao)"/><text x="380" y="257" class="s" text-anchor="middle">nudge (any time: reminders, alerts, coach), phone requests (calendar add, reply)</text>
</svg><figcaption>Figure D2. One authenticated WebSocket per app session carries the conversation both ways. Background
messages arrive as nudges; requests the server needs the phone for (add a calendar event, send a reply) go the same
way and come back with a result.</figcaption></figure>
"""

DEV_BODY = ARCH_STYLE + """
<h1>Developers and tech leads</h1>
<p class="lead">How Buddy's code is organised, how a turn runs, how to add a tool or a connector, and how changes are
tested and shipped. Read <a href="architecture.html">Architecture</a> first for the big picture.</p>
<p><span class="pill warn">Draft for review</span> Updated {UPDATED}.</p>

<h2 id="stack">Stack</h2>
<div class="table-wrap"><table>
<tr><th>Part</th><th>Technology</th></tr>
<tr><td>Server</td><td>Python 3.11, FastAPI on uvicorn (asyncio), one process under systemd</td></tr>
<tr><td>Storage</td><td>SQLite with SQLCipher (AES-256), one database file plus a separate encrypted access log</td></tr>
<tr><td>AI</td><td>Anthropic SDK (Claude: quick, standard and expert tiers), MCP for agent connectors, free models
through their own APIs, local embeddings for meaning search</td></tr>
<tr><td>Voice</td><td>faster-whisper on the server (optional hosted Whisper for Talk mode)</td></tr>
<tr><td>Calls</td><td>Pipecat pipeline: Silero voice activity, Deepgram, Claude, ElevenLabs; Plivo for the line</td></tr>
<tr><td>Android app</td><td>Kotlin 2, Jetpack Compose (Material 3), OkHttp WebSocket, Health Connect</td></tr>
<tr><td>Network</td><td>Tailscale (WireGuard) private network with HTTPS; Funnel only for the call-audio path</td></tr>
<tr><td>Sandbox</td><td>A separate local service running throwaway containers, reached over a local socket</td></tr>
<tr><td>CI</td><td>GitHub Actions: Android build, tests and security checks (pip-audit, bandit, gitleaks), Claude Code</td></tr>
</table></div>
<p>Size today: about 110 server modules (about 42,000 lines), about 900 server tests, about 9,000 lines of Kotlin.</p>

<h2 id="runtime">Runtime model</h2>
<ul class="ticks">
<li><strong>One process, one event loop.</strong> HTTP, the live link and the background loop share the asyncio loop.
Anything blocking (a database query, an outside call) runs in a worker thread with <code>asyncio.to_thread</code>.</li>
<li><strong>One shared database connection</strong> with a lock around multi-statement scripts; tables are created on
first use with <code>CREATE TABLE IF NOT EXISTS</code> (schema scripts run once per connection).</li>
<li><strong>The background loop</strong> (proactive) wakes every few minutes, runs each job in its own try/except so one
failure never stops the others, and delivers messages through one function that also prevents repeats.</li>
<li><strong>Outside calls</strong> all go through the gateway, with timeouts, one retry for reads and per-service rate
limits; a failure becomes a clear error the model can explain.</li>
</ul>

<h2 id="map">Code map</h2>
""" + LAYERS_SVG + """
<h2 id="modules">Key modules</h2>
<div class="table-wrap"><table>
<tr><th>Module</th><th>Responsibility</th></tr>
<tr><td><code>server</code></td><td>Routes: the live link, the app's API, admin endpoints, bank and connect pages, call
webhooks; startup jobs; delivering nudges to connected apps.</td></tr>
<tr><td><code>agent</code></td><td>The conversation: system prompt, the tool list (personal tools withheld from free
models), the tool loop, streaming, approvals, step-up to the expert model, <code>compose</code> for background writing.</td></tr>
<tr><td><code>router</code></td><td>Picks the model per turn by rules (no extra model call).</td></tr>
<tr><td><code>memory</code>, <code>db</code></td><td>Facts, goals, reminders, settings (key-value) over the encrypted database.</td></tr>
<tr><td><code>gateway</code></td><td>Buddy Connect: catalog, call path, status, health checks, "connect your own account".</td></tr>
<tr><td><code>proactive</code></td><td>The background loop: briefings, nudges, checks, learning.</td></tr>
<tr><td><code>thrift</code>, <code>budgets</code>, <code>usage</code></td><td>Asking for a yes before actions; spend caps; cost of every
call.</td></tr>
<tr><td><code>privacy</code>, <code>guard</code></td><td>Masking of numbers and IDs; access log, lockout, security headers.</td></tr>
<tr><td><code>calls</code></td><td>Phone agent: planning, Plivo, the audio pipeline, screening, costs.</td></tr>
<tr><td><code>oura</code>, <code>coach</code>, <code>health_data</code></td><td>Ring sync, the health plan and its nudges, phone health data.</td></tr>
</table></div>

<h2 id="protocol">App protocol (live link)</h2>
""" + PROTOCOL_SVG + """
<p>The app opens one WebSocket with the owner token. Messages are JSON objects with a <code>type</code>. A background
message can carry a short spoken form and a richer shown form (<code>spoken || shown</code>). The server remembers its
own address from the app's connection, so links it makes (bank linking, "connect your own account") point back to it.</p>

<h2 id="tools">Tools: how Buddy does things</h2>
<p>A tool is a JSON schema the model sees and a function that runs it. Each domain module exposes both:</p>
<pre><code>TOOL = {"name": "health_plan", "description": "When and how to use it, in plain words...",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string",
                         "enum": ["show", "today", "log"]}}, "required": ["action"]}}

def run_tool(memory, args: dict) -> str:   # returns text or JSON for the model
    ...</code></pre>
<ol>
<li>Write the module with <code>TOOL</code> and <code>run_tool</code>; keep decisions in code and words in the description.</li>
<li>Register it in the agent's tool list and dispatch. Personal tools must <strong>not</strong> be in the general
(free-model) list.</li>
<li>If it changes anything outside Buddy, ask first through <code>thrift.ask_action</code>; never act on its own.</li>
<li>Run blocking work with <code>asyncio.to_thread</code>; raise <code>ToolError</code> with a sentence the owner understands.</li>
<li>Add tests with a temporary database and a fake network; update the privacy policy if it stores or sends anything new.</li>
</ol>

<h2 id="models">Model routing</h2>
<div class="table-wrap"><table>
<tr><th>Turn</th><th>Model</th></tr>
<tr><td>Small talk alone ("thanks", "good night")</td><td>A free model, when enabled</td></tr>
<tr><td>Short chat, reminders, tasks, quick facts</td><td>Claude quick tier (about 20x cheaper); can hand over mid-turn</td></tr>
<tr><td>Email, money, places, planning</td><td>Claude standard tier</td></tr>
<tr><td>Hard questions</td><td>Claude expert tier, through an explicit step-up</td></tr>
<tr><td>Background writing (briefings, wrap-ups)</td><td>Quick tier with a small prompt, capped per day</td></tr>
</table></div>
<p>Rule: if a turn touches personal data, or a file was just uploaded, it stays on Claude.</p>

<h2 id="connector">Adding a connector</h2>
<ol>
<li><strong>Catalog entry</strong> in the gateway: group, title, used_for, hosts, auth kind, cost, per-minute limit,
privacy class, a health check. It then shows in status, costs and the app.</li>
<li><strong>Calls</strong> through <code>gateway.call(memory, name, path, ...)</code> only, with <code>personal=True</code>
when the request carries the owner's data (the gateway refuses that for public services).</li>
<li><strong>Secrets</strong> go in server settings by name; the owner enters them at a hidden prompt; code never sees
them in source, logs or replies.</li>
<li><strong>Per-user accounts</strong> (OAuth): add <code>"link": "&lt;module&gt;"</code> to the entry and give the module
<code>start_link</code>, <code>finish_link</code> and <code>linked</code>. The app shows Connect; the server builds the
sign-in link with a one-time state and receives the browser back on its own private page. Tokens go in the encrypted
database. If the provider rotates refresh tokens, save the new one before anything else, under a lock.</li>
<li><strong>Syncing</strong>: a <code>sync_if_due</code> that never raises, called from the background loop and before
the tool answers.</li>
</ol>

<h2 id="background">Background work</h2>
<p>Each job in the proactive loop is self-contained: it checks whether it's due (a date or hour stored in settings),
does its work in a thread, and returns messages as <code>{"key", "text", "urgent"}</code>. The loop skips quiet hours
and holds non-urgent messages while the owner is driving or in a meeting; a key is sent once. Jobs must be cheap when
there's nothing to do and must never raise.</p>

<h2 id="datamodel">Data model</h2>
<div class="table-wrap"><table>
<tr><th>Area</th><th>Main tables</th></tr>
<tr><td>Core</td><td>facts, goals, reminders, settings (key-value), conversations</td></tr>
<tr><td>Usage</td><td>model_calls, api_calls (provider, endpoint, ok, seconds, cost)</td></tr>
<tr><td>Money</td><td>plaid_items, plaid_accounts, transactions, bills, cards</td></tr>
<tr><td>Health</td><td>health_daily (phone), oura_daily (ring), coach_log, coach_meals</td></tr>
<tr><td>Places</td><td>location history, named places, learned routine (in settings)</td></tr>
<tr><td>Calls</td><td>calls (outcomes and costs, no audio), phone_contacts, saved_callers</td></tr>
</table></div>
<p>Each table has a retention rule enforced in code (see the privacy policy); deleting a connection deletes its data.</p>

<h2 id="config">Settings</h2>
<p>All configuration is environment settings named <code>BUDDY_*</code>, kept sealed on the server and entered with a
hidden prompt. Non-secret tuning (for example call prices or meeting hours) uses the same mechanism. Code reads them
through one helper so tests can pass their own values.</p>

<h2 id="testing">Testing</h2>
<ul class="ticks">
<li>Run: <code>python -m pytest -q</code> in the backend; about a minute for the whole suite.</li>
<li>Every test uses its own temporary database; nothing touches the network: the gateway takes a <code>fetch</code>
function, so tests pass a fake that answers by address and records what was sent.</li>
<li>Time-dependent logic takes <code>now</code> as a parameter.</li>
<li>Test names say the behaviour in a sentence ("refresh tokens rotate and the newest is kept").</li>
<li>Privacy is tested too: for example that a location never reaches a search service.</li>
</ul>

<h2 id="ci">CI and release</h2>
<div class="table-wrap"><table>
<tr><th>Workflow</th><th>What it checks</th></tr>
<tr><td>Security checks</td><td>All tests; known vulnerabilities in dependencies; static code scan; leaked secrets</td></tr>
<tr><td>Android app</td><td>Builds the app on every push</td></tr>
<tr><td>Claude Code</td><td>Works on issues Buddy files about itself and proposes fixes for review</td></tr>
</table></div>
<p>Release: the owner pulls and runs the update script on the server (dependencies, restart); the app is installed
from the CI build. Self-made changes can't touch protected areas (security, budgets, deploy).</p>

<h2 id="debug">Debugging</h2>
<ul class="ticks">
<li>The service log (systemd journal) holds warnings and errors; never message bodies or secrets.</li>
<li>Connections in the app shows each service's last result, reason and a live Test.</li>
<li>Insights shows cost, latency and errors per day; a daily health check summarises problems.</li>
<li>Call debugging is opt-in (a setting), logs what was heard and decided for testing, and should be switched off after.</li>
</ul>

<h2 id="checklist">Change checklist</h2>
<ul class="ticks">
<li>Does it store or send a new kind of data? Update the privacy policy (data, provider, retention).</li>
<li>Is it personal? Keep it off free models and out of logs; mark gateway calls <code>personal=True</code>.</li>
<li>Does it act in the world? Ask for a yes; respect budgets.</li>
<li>Does it call out? Through the gateway, with a catalog entry and a health check.</li>
<li>Does it run in the background? Due-check, never raise, quiet hours, once per key.</li>
<li>Tests pass; the Android build passes; no secrets in the diff.</li>
</ul>

<h2 id="lead">For tech leads</h2>
<div class="table-wrap"><table>
<tr><th>Topic</th><th>Current position</th><th>Watch</th></tr>
<tr><td>Isolation</td><td>One server per owner; no shared backend</td><td>Ops cost per user as users grow</td></tr>
<tr><td>Scaling</td><td>Single process; outside calls in threads</td><td>Event-loop stalls: log lines flag slow audio loops on calls</td></tr>
<tr><td>Cost</td><td>Routing, caching, gates, per-call costing, budgets</td><td>Monthly per-service totals in Connections</td></tr>
<tr><td>Vendor risk</td><td>Every vendor behind the gateway; backups for public data</td><td>Single providers for calls and banking</td></tr>
<tr><td>AI risk</td><td>Action boundary; outside content as data; masking</td><td>Prompt injection through email and web</td></tr>
<tr><td>Quality</td><td>About 900 tests, CI on every push, security scans</td><td>No end-to-end tests on a real phone yet</td></tr>
<tr><td>Compliance</td><td>Privacy policy kept in step with code</td><td>Pen test, SOC 2 and DPAs in progress</td></tr>
</table></div>
"""

docs_page("developers.html", f"Developers · Buddy · {CO}", DEV_BODY.replace("{UPDATED}", DOCS_UPDATED), side=DEV_SIDE)
