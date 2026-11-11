# Buddy Connect API documentation: /docs/ on the site, behind the password login (functions/docs/_middleware.js).
# Run by pages.py (through mksite.py), so CO, EMAIL, SITE and UPDATED are defined. Never put a server address,
# token, key or password here: the pages are served only after sign-in, but they live in the site's repo.
import os

DOCS_UPDATED = "November 11, 2026"


def docs_page(file, title, body, nav=True, side=None):
    os.makedirs("docs", exist_ok=True)
    side = side or """
<aside class="docnav"><strong>Buddy Connect API</strong>
<a href="#overview">Overview</a><a href="#concepts">Concepts</a><a href="#groups">Groups and services</a>
<a href="#privacy">Privacy rules</a><a href="#reliability">Reliability</a><a href="#auth">Authentication</a>
<a href="#http">HTTP API</a><a href="#errors">Errors</a><a href="#python">Using it inside Buddy</a>
<a href="#add">Adding a provider</a><a href="#apps">Connecting another app</a><a href="#limits">Limits and versions</a>
<a href="architecture.html">Architecture</a><a href="developers.html">Developers</a>
<a href="/api/docs-login?logout=1">Sign out</a></aside>""" if nav else ""
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>
<link rel="icon" href="../favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="../styles.css">
</head>
<body>
<header class="site"><div class="wrap">
  <a class="logo" href="../index.html"><span class="mark" aria-hidden="true">B</span><span>{CO}</span></a>
  <nav class="site" aria-label="Main"><a href="../connect.html">Buddy Connect</a><a href="index.html"{' aria-current="page"' if file == "index.html" else ""}>API documentation</a><a href="architecture.html"{' aria-current="page"' if file == "architecture.html" else ""}>Architecture</a><a href="developers.html"{' aria-current="page"' if file == "developers.html" else ""}>Developers</a></nav>
</div></header>
<main><div class="wrap {'docs' if nav else ''}">{side}<article class="doc">
{body}
</article></div></main>
<footer class="site"><div class="wrap"><span>© 2026 {CO}. Confidential: for review and approved partners. Updated {DOCS_UPDATED}.</span></div></footer>
</body>
</html>
"""
    open(os.path.join("docs", file), "w").write(html)


docs_page("login.html", f"Sign in · Buddy Connect API · {CO}", f"""
<section class="block"><div class="login">
  <h1>Buddy Connect API</h1>
  <p class="lead">The documentation is for approved partners. Enter the access password.</p>
  <form method="post" action="/api/docs-login" class="waitlist">
    <label><span>Password</span><input type="password" name="password" required autocomplete="current-password" autofocus></label>
    <button class="btn primary" type="submit">Sign in</button>
  </form>
  <p class="fine" id="msg"></p>
  <script>if (location.search.includes("wrong")) document.getElementById("msg").textContent = "That password isn't right.";
  if (location.search.includes("busy")) document.getElementById("msg").textContent = "Too many tries. Please try again in an hour.";
  if (location.search.includes("off")) document.getElementById("msg").textContent = "Sign-in isn't open yet.";</script>
  <p class="fine">No access? <a href="mailto:{EMAIL}?subject=Buddy%20Connect%20access">Ask for it</a>.</p>
</div></section>
""", nav=False)

docs_page("index.html", f"Buddy Connect API · {CO}", f"""
<h1 id="overview">Buddy Connect API</h1>
<p class="lead">Buddy Connect is the single gateway between Buddy and every outside service it uses. One catalog
describes each service; one call path sends every request; one status shows what works, what doesn't and why.
This guide explains how it is built, how Buddy uses it, and how another app connects to it.</p>
<div class="grid">
  <div class="card"><h3>Catalog</h3><p>Every service, its address, sign-in, cost, limits, privacy class and group.</p></div>
  <div class="card"><h3>Gateway</h3><p>One way to call: keys and tokens, backups, retries, limits, logging, clear errors.</p></div>
  <div class="card"><h3>Status</h3><p>Set up or not, last result and reason, calls and cost this month, a live test.</p></div>
</div>

<h2 id="concepts">Concepts</h2>
<h3>Service (catalog entry)</h3>
<p>Each outside service is one entry, named in lower case (<code>njt_rail</code>, <code>alpaca</code>,
<code>openfda</code>). An entry says:</p>
<table><tr><th>Field</th><th>Meaning</th></tr>
<tr><td><code>group</code></td><td>The area it belongs to (see <a href="#groups">Groups</a>).</td></tr>
<tr><td><code>title</code>, <code>used_for</code></td><td>A name and what Buddy uses it for, shown to the owner.</td></tr>
<tr><td><code>hosts</code></td><td>Its https addresses, in order. Later ones are backups, tried only when the first can't be reached (unless <code>no_backup</code>).</td></tr>
<tr><td><code>auth</code></td><td>How it signs in: <code>none</code>; <code>header</code> (a key in a header); <code>param</code> (a key in the address); <code>oauth</code> (a saved sign-in renewed automatically, such as Google's); <code>token</code> (a username and password exchanged for a token that is reused); <code>phone</code> (data read on the owner's phone).</td></tr>
<tr><td><code>secret</code> / <code>fields</code></td><td>The names of the settings that hold the key, or the username and password. The values never appear in code, logs or this API.</td></tr>
<tr><td><code>privacy</code></td><td><code>public</code> or <code>personal</code> (see <a href="#privacy">Privacy rules</a>).</td></tr>
<tr><td><code>cost</code>, <code>per_minute</code></td><td>Cost per call in USD, and the most calls allowed per minute.</td></tr>
<tr><td><code>health</code></td><td>A small real request that proves the service works (the Test button).</td></tr>
</table>

<h2 id="groups">Groups and services</h2>
<table><tr><th>Group</th><th>Services</th></tr>
<tr><td>Travel &amp; transit</td><td>NJ Transit trains; NJ Transit buses and light rail; MTA; PATH; AirLabs (flights)</td></tr>
<tr><td>Finance &amp; markets</td><td>Plaid (banks, read-only); Alpaca; Yahoo Finance; Stooq; Coinbase; StockTwits</td></tr>
<tr><td>Shopping</td><td>Google Shopping (via Serper); Keepa (Amazon price history)</td></tr>
<tr><td>Health</td><td>Phone health data (Google Health Connect); openFDA; MedlinePlus; Open Food Facts</td></tr>
<tr><td>Home</td><td>Google Nest (thermostat; changes only after the owner's yes)</td></tr>
<tr><td>Search</td><td>Serper (Google search, news, places)</td></tr>
<tr><td>News &amp; trends</td><td>Reddit; Hacker News; arXiv; GitHub; SerpApi (Google Trends)</td></tr>
<tr><td>Weather</td><td>Open-Meteo</td></tr>
<tr><td>AI models</td><td>Claude; free models from Groq, Cerebras, Gemini, Mistral, OpenRouter and GitHub Models</td></tr>
</table>

<h2 id="privacy">Privacy rules</h2>
<ul class="ticks">
<li><strong>public</strong> services only ever receive general requests: a search, a line name, a product, a medicine name. Never the owner's data.</li>
<li><strong>personal</strong> services may handle the owner's data: Claude (the only AI that sees personal data), Plaid (the owner's own banks, read-only), Google Nest (the owner's own thermostat) and the phone's health data (which stays on the owner's server).</li>
<li>Anything that changes the owner's world (a thermostat setting, a form, a payment) happens only after the owner's yes.</li>
<li>A call that carries personal data must say so (<code>personal=True</code>); the gateway refuses it for a public service.</li>
<li>Keys and passwords are read from the server's settings at call time, never logged, never returned. When a key travels in the address, only the path is logged.</li>
<li>Logs keep the time, service, path, result, cost and the service's own error text, for the owner's cost and status views.</li>
</ul>

<h2 id="reliability">Reliability</h2>
<ul class="ticks">
<li><strong>Backups:</strong> the next host is tried only when one can't be reached.</li>
<li><strong>Retries:</strong> one retry on a server error (5xx) or timeout, for reads only. A sign-in or a post is never re-sent.</li>
<li><strong>Tokens:</strong> minted once and reused for their life (23 hours by default). A refused sign-in isn't retried within the hour, so services that limit sign-ins aren't locked.</li>
<li><strong>Rate limits:</strong> each service's <code>per_minute</code>; beyond it the call is refused with HTTP 429 instead of hammering the service.</li>
<li><strong>Clear errors:</strong> failures come back with the status and the service's own words, for example <em>"Serper said HTTP 400: Not enough credits"</em>.</li>
</ul>

<h2 id="auth">Authentication</h2>
<p>Buddy Connect runs on the owner's own Buddy server, reached over the owner's private network. Every HTTP request
carries the owner's access token:</p>
<pre><code>Authorization: Bearer &lt;your access token&gt;</code></pre>
<p>Tokens are issued by the owner for their own apps. Repeated failed tokens lock the caller out for a while.</p>

<h2 id="http">HTTP API</h2>
<h3>GET /admin/connections</h3>
<p>Every service and its state.</p>
<pre><code>{{"services": [
  {{"name": "njt_rail", "group": "Travel &amp; transit", "title": "NJ Transit trains (RailData)",
   "used_for": "NJ Transit train service messages", "privacy": "public",
   "set_up": true, "in_connect": true,
   "last_ok": false, "last_at": "2026-11-10T14:02:11Z",
   "last_error": "HTTP 500: Missing user account.",
   "calls_this_month": 41, "failed_this_month": 3, "cost_this_month": 0.0,
   "can_test": true,
   "setup": "set.sh BUDDY_NJT_USERNAME; set.sh BUDDY_NJT_PASSWORD"}}
]}}</code></pre>
<p><code>last_ok</code> is <code>true</code>, <code>false</code> or <code>null</code> (not used yet). <code>setup</code> is the
command that attaches a service that isn't set up.</p>

<h3>POST /admin/connections/test</h3>
<p>One small real request to one service.</p>
<pre><code>Request:  {{"name": "openfda"}}
Response: {{"name": "openfda", "ok": true, "said": "ok"}}</code></pre>

<h3>POST /api/health/sync</h3>
<p>Daily health totals from the owner's phone (Health Connect), kept 90 days on the server.</p>
<pre><code>{{"days": [
  {{"day": "2026-11-09", "steps": 8123, "sleep_minutes": 412, "resting_hr": 58, "avg_hr": 71,
   "active_minutes": 34, "calories": 2210, "workouts": [{{"type": "running", "minutes": 30}}]}}
]}}
Response: {{"ok": true, "days": 1}}</code></pre>

<h2 id="errors">Errors</h2>
<table><tr><th>HTTP</th><th>Meaning</th></tr>
<tr><td>401</td><td>Missing or wrong access token.</td></tr>
<tr><td>404</td><td>Unknown service name (test).</td></tr>
<tr><td>429</td><td>Too many calls: wait and try again.</td></tr>
<tr><td>5xx</td><td>The server or a service failed; the body says which and why.</td></tr>
</table>
<p>Inside Buddy, a failed call raises <code>ConnectError</code> with <code>provider</code>, <code>status</code> (0 when a
service couldn't be reached) and <code>reason</code> (the service's own words).</p>

<h2 id="python">Using it inside Buddy</h2>
<pre><code>from buddy import gateway

# By name and path: hosts, key and backups come from the catalog
results = gateway.call(memory, "serper", "/search", json_body={{"q": "weather"}})

# A full address (when a module builds it), logged under the service's name
data = gateway.call_url(memory, "alpaca", [url], headers=headers, purpose="markets")

# A sign-in token, minted once and reused
tok = gateway.token(memory, "njt_bus")

# What can do a job, and how to attach it if it isn't
gateway.find("train delays", memory)

# Status and a live check
gateway.status(memory);  gateway.health("openfda", memory)

# A service that sends on its own (the free models) reports its last result
gateway.report(memory, "groq", ok=False, error="at its free limit")</code></pre>
<p>Buddy itself uses the <code>connections</code> tool (<em>list</em>, <em>find</em>, <em>test</em>): when a task needs
a service that isn't attached, it names the service and the one command to attach it, and carries on.</p>

<h2 id="add">Adding a provider</h2>
<ol>
<li>Add one catalog entry with its group, hosts, sign-in, privacy class, cost, limit and a health check:
<pre><code>"openfda": {{"group": "Health", "title": "openFDA (medicines)",
            "hosts": ["https://api.fda.gov"], "auth": {{"type": "none"}},
            "cost": 0.0, "per_minute": 40, "privacy": "public",
            "used_for": "what a medicine is for, warnings, interactions, recalls",
            "health": {{"path": "/drug/label.json?search=openfda.generic_name:ibuprofen&amp;limit=1"}}}}</code></pre></li>
<li>Call it with <code>gateway.call(memory, "openfda", "/drug/label.json?...")</code>.</li>
<li>If it needs a key, the owner saves it on the server with <code>set.sh</code> at a hidden prompt.</li>
<li>Add it to the privacy policy if it receives anything new.</li>
</ol>
<p>It appears at once in Buddy's status, the app's Connections screen (in its group, with a Test button), cost
reports and <code>find</code>.</p>

<h2 id="apps">Connecting another app</h2>
<p>An app the owner approves connects the same way the Buddy app does:</p>
<ol>
<li>The owner issues it an access token and gives it the server's private address.</li>
<li>It reads <code>GET /admin/connections</code> to show what's available and working.</li>
<li>It runs <code>POST /admin/connections/test</code> to check a service.</li>
<li>It sends data the owner allows (for example health totals) to the matching endpoint.</li>
</ol>
<p>Apps never receive a service's keys: requests to outside services always go through Buddy Connect on the owner's
server, under the privacy rules above.</p>

<h2 id="limits">Limits and versions</h2>
<ul class="ticks">
<li>Per-service limits as in the catalog; the server's own limits apply to the HTTP API.</li>
<li>Responses only add fields over time; existing fields keep their meaning.</li>
<li>Questions: <a href="mailto:{EMAIL}?subject=Buddy%20Connect%20API">{EMAIL}</a>.</li>
</ul>
""")

exec(open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "architecture.py")).read())
exec(open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "developers.py")).read())
