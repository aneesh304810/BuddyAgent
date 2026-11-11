# Buddy's end-to-end architecture, for technical review (CTO, security reviewers, approved partners): /docs/architecture
# Run by apidocs.py, so docs_page, CO, EMAIL are defined. Names services and how Buddy works (this is inside /docs/),
# but never a server address, port, token, key, password or file path.

ARCH_SIDE = """
<aside class="docnav"><strong>Architecture</strong>
<a href="#summary">Summary</a><a href="#principles">Design principles</a><a href="#overview">System overview</a>
<a href="#journey">A request, end to end</a><a href="#components">Components</a><a href="#connect">Buddy Connect</a>
<a href="#calls">Phone agent</a><a href="#proactive">Proactive engine</a><a href="#data">Data and retention</a>
<a href="#security">Security architecture</a><a href="#threats">Threats and mitigations</a>
<a href="#ops">Operations</a><a href="#gaps">Known gaps</a><a href="#review">Review questions</a>
<a href="developers.html">Developers</a><a href="index.html">API documentation</a></aside>"""

ARCH_STYLE = """
<style>
.dia { margin: 20px 0 8px; border: 1px solid var(--line); border-radius: 14px; background: #fbfaf7; padding: 10px; }
.dia svg { width: 100%; height: auto; display: block; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.dia figcaption { color: var(--muted); font-size: 14px; padding: 6px 6px 2px; }
.dia .box { fill: #fff; stroke: #d9d5cc; stroke-width: 1.2; }
.dia .srv { fill: #f2f8f6; stroke: #1f6f5c; stroke-width: 1.6; }
.dia .mod { fill: #fff; stroke: #1f6f5c; stroke-width: 1.2; }
.dia .ext { fill: #fff8ee; stroke: #c8862a; stroke-width: 1.2; }
.dia .net { fill: #e5f2ee; stroke: none; }
.dia .sec { fill: #fbebeb; stroke: #b3261e; stroke-width: 1.2; }
.dia .t { font-size: 13px; font-weight: 650; fill: #16181d; }
.dia .s { font-size: 11px; fill: #5b616e; }
.dia .w { font-size: 12px; font-weight: 650; fill: #1f6f5c; }
.dia .line { stroke: #9aa1ad; stroke-width: 1.4; fill: none; }
.dia .flow { stroke: #1f6f5c; stroke-width: 2; fill: none; stroke-dasharray: 6 8; animation: dash 1.1s linear infinite; }
.dia .flow.pub { stroke: #c8862a; }
.dia .flow.red { stroke: #b3261e; }
@keyframes dash { to { stroke-dashoffset: -28; } }
@media (prefers-reduced-motion: reduce) { .dia .flow { animation: none; } }
.pill { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
        background: var(--brand-soft); color: var(--brand); }
.pill.warn { background: #f9eeda; color: #8a5a12; }
.pill.bad { background: #fbebeb; color: #b3261e; }
</style>
<svg width="0" height="0" style="position:absolute"><defs>
<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" fill="#1f6f5c"/></marker>
<marker id="ag" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" fill="#9aa1ad"/></marker>
<marker id="ao" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0,0 L10,5 L0,10 z" fill="#c8862a"/></marker>
</defs></svg>
"""

OVERVIEW_SVG = """
<figure class="dia" id="fig-overview"><svg viewBox="0 0 760 540" role="img" aria-labelledby="ovt">
<title id="ovt">System overview: the phone app and web pages reach the owner's Buddy server over a private network;
the server talks to Claude, free models (public questions only), Buddy Connect services, the call providers and GitHub.</title>
<text x="80" y="28" class="w" text-anchor="middle">YOU</text>
<rect x="10" y="80" width="140" height="80" rx="12" class="box"/><text x="80" y="106" class="t" text-anchor="middle">Buddy app</text>
<text x="80" y="124" class="s" text-anchor="middle">Android: chat, voice,</text><text x="80" y="138" class="s" text-anchor="middle">phone bridge, health,</text>
<text x="80" y="152" class="s" text-anchor="middle">contacts, location</text>
<rect x="10" y="180" width="140" height="56" rx="12" class="box"/><text x="80" y="204" class="t" text-anchor="middle">Web pages</text>
<text x="80" y="222" class="s" text-anchor="middle">money, cards, reports</text>
<rect x="10" y="400" width="140" height="62" rx="12" class="box"/><text x="80" y="425" class="t" text-anchor="middle">Phone calls</text>
<text x="80" y="443" class="s" text-anchor="middle">people calling you,</text><text x="80" y="456" class="s" text-anchor="middle">numbers Buddy calls</text>

<rect x="163" y="60" width="34" height="330" rx="10" class="net"/>
<text transform="translate(185 305) rotate(-90)" class="w" text-anchor="middle">Private network</text>
<rect x="545" y="404" width="22" height="52" rx="6" class="sec"/>
<text x="556" y="398" class="s" text-anchor="middle">signed</text>

<rect x="210" y="40" width="330" height="440" rx="16" class="srv"/>
<text x="375" y="30" class="w" text-anchor="middle">YOUR BUDDY SERVER (one per owner)</text>
<rect x="225" y="70" width="145" height="62" rx="10" class="mod"/><text x="297" y="93" class="t" text-anchor="middle">Front door</text>
<text x="297" y="110" class="s" text-anchor="middle">API, live link, sign-in,</text><text x="297" y="123" class="s" text-anchor="middle">lockout, access log</text>
<rect x="385" y="70" width="140" height="62" rx="10" class="mod"/><text x="455" y="93" class="t" text-anchor="middle">Brain</text>
<text x="455" y="110" class="s" text-anchor="middle">agent, model router,</text><text x="455" y="123" class="s" text-anchor="middle">tools, privacy guard</text>
<rect x="225" y="150" width="145" height="62" rx="10" class="mod"/><text x="297" y="173" class="t" text-anchor="middle">Memory</text>
<text x="297" y="190" class="s" text-anchor="middle">encrypted database:</text><text x="297" y="203" class="s" text-anchor="middle">facts, tasks, data</text>
<rect x="385" y="150" width="140" height="62" rx="10" class="mod"/><text x="455" y="173" class="t" text-anchor="middle">Buddy Connect</text>
<text x="455" y="190" class="s" text-anchor="middle">one gateway to every</text><text x="455" y="203" class="s" text-anchor="middle">outside service</text>
<rect x="225" y="230" width="145" height="62" rx="10" class="mod"/><text x="297" y="253" class="t" text-anchor="middle">Proactive engine</text>
<text x="297" y="270" class="s" text-anchor="middle">briefings, nudges,</text><text x="297" y="283" class="s" text-anchor="middle">reminders, learning</text>
<rect x="385" y="230" width="140" height="62" rx="10" class="mod"/><text x="455" y="253" class="t" text-anchor="middle">Code sandbox</text>
<text x="455" y="270" class="s" text-anchor="middle">throwaway containers;</text><text x="455" y="283" class="s" text-anchor="middle">Buddy never runs code</text>
<rect x="225" y="310" width="145" height="62" rx="10" class="mod"/><text x="297" y="333" class="t" text-anchor="middle">Operations</text>
<text x="297" y="350" class="s" text-anchor="middle">backups, health checks,</text><text x="297" y="363" class="s" text-anchor="middle">key-age reminders</text>
<rect x="385" y="310" width="140" height="62" rx="10" class="mod"/><text x="455" y="333" class="t" text-anchor="middle">Self-improve</text>
<text x="455" y="350" class="s" text-anchor="middle">issues to Claude Code,</text><text x="455" y="363" class="s" text-anchor="middle">reviewed, tested</text>
<rect x="225" y="392" width="300" height="72" rx="10" class="mod"/><text x="375" y="416" class="t" text-anchor="middle">Phone agent</text>
<text x="375" y="434" class="s" text-anchor="middle">listens, thinks, speaks on calls; answers missed calls;</text>
<text x="375" y="448" class="s" text-anchor="middle">never pays, books or shares more than allowed</text>

<text x="660" y="28" class="w" text-anchor="middle">OUTSIDE</text>
<rect x="580" y="45" width="170" height="50" rx="12" class="ext"/><text x="665" y="66" class="t" text-anchor="middle">Claude (Anthropic)</text>
<text x="665" y="83" class="s" text-anchor="middle">all personal reasoning</text>
<rect x="580" y="105" width="170" height="50" rx="12" class="ext"/><text x="665" y="126" class="t" text-anchor="middle">Free AI models</text>
<text x="665" y="143" class="s" text-anchor="middle">public questions only</text>
<rect x="580" y="170" width="170" height="150" rx="12" class="ext"/><text x="665" y="192" class="t" text-anchor="middle">Connected services</text>
<text x="665" y="212" class="s" text-anchor="middle">Finance: Plaid, markets</text><text x="665" y="228" class="s" text-anchor="middle">Health: Oura, openFDA</text>
<text x="665" y="244" class="s" text-anchor="middle">Travel and transit</text><text x="665" y="260" class="s" text-anchor="middle">Home: Nest</text>
<text x="665" y="276" class="s" text-anchor="middle">Search, news, weather</text><text x="665" y="292" class="s" text-anchor="middle">Email, calendar (read)</text>
<text x="665" y="308" class="s" text-anchor="middle">+ more in the catalog</text>
<rect x="580" y="335" width="170" height="44" rx="12" class="ext"/><text x="665" y="354" class="t" text-anchor="middle">GitHub</text>
<text x="665" y="370" class="s" text-anchor="middle">code, reviews, CI</text>
<rect x="580" y="395" width="170" height="70" rx="12" class="ext"/><text x="665" y="416" class="t" text-anchor="middle">Call providers</text>
<text x="665" y="433" class="s" text-anchor="middle">Plivo (line), Deepgram</text><text x="665" y="447" class="s" text-anchor="middle">(hearing), ElevenLabs</text>
<text x="665" y="460" class="s" text-anchor="middle">(voice)</text>

<path class="flow" d="M150,120 H225" marker-end="url(#ah)"/>
<path class="flow" d="M150,208 H180 V115 H225" marker-end="url(#ah)"/>
<path class="flow" d="M370,101 H385" marker-end="url(#ah)"/>
<path class="line" d="M330,150 L400,132" marker-end="url(#ag)" marker-start="url(#ag)"/>
<path class="line" d="M455,132 V150" marker-end="url(#ag)"/>
<path class="line" d="M370,261 H378 V122 H385" marker-end="url(#ag)"/>
<path class="line" d="M455,212 V230" marker-end="url(#ag)"/>
<path class="flow" d="M525,88 H552 V70 H580" marker-end="url(#ah)"/>
<path class="flow pub" d="M525,118 H552 V130 H580" marker-end="url(#ao)"/>
<path class="flow" d="M525,181 H552 V230 H580" marker-end="url(#ah)"/>
<path class="line" d="M525,341 H552 V357 H580" marker-end="url(#ag)"/>
<path class="flow" d="M580,430 H567" marker-end="url(#ah)"/>
<path class="flow" d="M545,430 H525" marker-end="url(#ah)"/>
<path class="flow" d="M80,462 V510 H665 V465" marker-end="url(#ah)"/>
<text x="372" y="526" class="s" text-anchor="middle">the phone network carries calls to the call provider, which streams audio to Buddy over the signed path</text>
</svg><figcaption>Figure 1. Everything runs on the owner's own server. The app and web pages reach it only over the private
network; the only public path is the signed one for live call audio. Green: personal data allowed. Amber: public
data only.</figcaption></figure>
"""

JOURNEY_SVG = """
<figure class="dia" id="fig-journey"><svg viewBox="0 0 760 330" role="img" aria-labelledby="jt">
<title id="jt">A request end to end: you speak, the server turns it into text, checks who you are, the router picks a
model, tools gather facts through Buddy Connect and memory, the answer streams back and is spoken.</title>
<rect x="10" y="30" width="130" height="80" rx="12" class="box"/><text x="75" y="55" class="t" text-anchor="middle">1. You ask</text>
<text x="75" y="73" class="s" text-anchor="middle">"Can I afford the</text><text x="75" y="87" class="s" text-anchor="middle">Tahoe trip?" (voice</text>
<text x="75" y="101" class="s" text-anchor="middle">or text)</text>
<rect x="165" y="30" width="130" height="80" rx="12" class="mod"/><text x="230" y="55" class="t" text-anchor="middle">2. Voice to text</text>
<text x="230" y="73" class="s" text-anchor="middle">on your server</text><text x="230" y="87" class="s" text-anchor="middle">(Talk mode may use a</text>
<text x="230" y="101" class="s" text-anchor="middle">fast hosted Whisper)</text>
<rect x="320" y="30" width="130" height="80" rx="12" class="mod"/><text x="385" y="55" class="t" text-anchor="middle">3. Front door</text>
<text x="385" y="73" class="s" text-anchor="middle">token checked,</text><text x="385" y="87" class="s" text-anchor="middle">lockout, access log</text>
<text x="385" y="101" class="s" text-anchor="middle">(no message bodies)</text>
<path d="M545,30 L610,70 L545,110 L480,70 Z" class="mod"/><text x="545" y="66" class="t" text-anchor="middle">4. Router</text>
<text x="545" y="82" class="s" text-anchor="middle">personal?</text>
<rect x="630" y="20" width="120" height="44" rx="12" class="ext"/><text x="690" y="38" class="t" text-anchor="middle">Claude</text>
<text x="690" y="54" class="s" text-anchor="middle">yes, or any doubt</text>
<rect x="630" y="78" width="120" height="44" rx="12" class="ext"/><text x="690" y="96" class="t" text-anchor="middle">Free model</text>
<text x="690" y="112" class="s" text-anchor="middle">public only</text>

<rect x="480" y="180" width="270" height="92" rx="12" class="mod"/><text x="615" y="204" class="t" text-anchor="middle">5. Tools</text>
<text x="615" y="222" class="s" text-anchor="middle">memory (balances, bills, calendar),</text>
<text x="615" y="236" class="s" text-anchor="middle">Buddy Connect (Plaid, weather, transit...),</text>
<text x="615" y="250" class="s" text-anchor="middle">actions only after your yes</text>
<text x="615" y="264" class="s" text-anchor="middle">(pay, book, send, call: never alone)</text>
<rect x="250" y="180" width="200" height="92" rx="12" class="mod"/><text x="350" y="204" class="t" text-anchor="middle">6. Answer</text>
<text x="350" y="222" class="s" text-anchor="middle">streamed back over the</text><text x="350" y="236" class="s" text-anchor="middle">live link; cost and turn</text>
<text x="350" y="250" class="s" text-anchor="middle">recorded (no transcript</text><text x="350" y="264" class="s" text-anchor="middle">sent anywhere)</text>
<rect x="10" y="180" width="200" height="92" rx="12" class="box"/><text x="110" y="204" class="t" text-anchor="middle">7. You hear it</text>
<text x="110" y="222" class="s" text-anchor="middle">spoken and shown with</text><text x="110" y="236" class="s" text-anchor="middle">cards (numbers, links,</text>
<text x="110" y="250" class="s" text-anchor="middle">what Buddy would do</text><text x="110" y="264" class="s" text-anchor="middle">and the yes button)</text>

<path class="flow" d="M140,70 H165" marker-end="url(#ah)"/>
<path class="flow" d="M295,70 H320" marker-end="url(#ah)"/>
<path class="flow" d="M450,70 H480" marker-end="url(#ah)"/>
<path class="flow" d="M610,62 L630,45" marker-end="url(#ah)"/>
<path class="flow pub" d="M610,78 L630,98" marker-end="url(#ao)"/>
<path class="flow" d="M690,122 V180" marker-end="url(#ah)"/>
<path class="line" d="M690,180 V64" marker-end="url(#ag)" stroke-dasharray="3 4"/>
<path class="flow" d="M480,226 H450" marker-end="url(#ah)"/>
<path class="flow" d="M250,226 H210" marker-end="url(#ah)"/>
<text x="380" y="310" class="s" text-anchor="middle">Typical turn: 1 to 3 seconds. The cheapest model that does the job well answers; hard questions step up.</text>
</svg><figcaption>Figure 2. One turn, end to end. The router keeps anything personal on Claude; a free model only ever sees a
general question with nothing about the owner in it.</figcaption></figure>
"""

CONNECT_SVG = """
<figure class="dia" id="fig-connect"><svg viewBox="0 0 760 360" role="img" aria-labelledby="ct">
<title id="ct">Buddy Connect: every outside call goes through one gateway with a catalog, keys, privacy check, limits
and retries, logging and cost, and status, to services grouped by area.</title>
<rect x="10" y="120" width="130" height="110" rx="12" class="mod"/><text x="75" y="146" class="t" text-anchor="middle">Buddy's tools</text>
<text x="75" y="166" class="s" text-anchor="middle">money, health, travel,</text><text x="75" y="180" class="s" text-anchor="middle">home, calls, search,</text>
<text x="75" y="194" class="s" text-anchor="middle">background checks,</text><text x="75" y="208" class="s" text-anchor="middle">the app's Test button</text>
<rect x="180" y="30" width="330" height="300" rx="16" class="srv"/><text x="345" y="56" class="w" text-anchor="middle">BUDDY CONNECT GATEWAY</text>
<rect x="200" y="70" width="290" height="34" rx="8" class="mod"/><text x="212" y="92" class="s"><tspan class="t" font-size="12">Catalog</tspan>  addresses, sign-in, cost, limit, privacy</text>
<rect x="200" y="112" width="290" height="34" rx="8" class="mod"/><text x="212" y="134" class="s"><tspan class="t" font-size="12">Keys</tspan>  on the server only; never in code or logs</text>
<rect x="200" y="154" width="290" height="34" rx="8" class="sec"/><text x="212" y="176" class="s"><tspan class="t" font-size="12">Privacy check</tspan>  personal data only where allowed</text>
<rect x="200" y="196" width="290" height="34" rx="8" class="mod"/><text x="212" y="218" class="s"><tspan class="t" font-size="12">Limits and retries</tspan>  caps per minute; one retry</text>
<rect x="200" y="238" width="290" height="34" rx="8" class="mod"/><text x="212" y="260" class="s"><tspan class="t" font-size="12">Log and cost</tspan>  result, time, cost; ids redacted</text>
<rect x="200" y="280" width="290" height="34" rx="8" class="mod"/><text x="212" y="302" class="s"><tspan class="t" font-size="12">Status</tspan>  working or not, and why; live test</text>
<rect x="550" y="30" width="200" height="300" rx="12" class="ext"/><text x="650" y="54" class="t" text-anchor="middle">Areas (groups)</text>
<text x="650" y="80" class="s" text-anchor="middle">Travel and transit</text><text x="650" y="102" class="s" text-anchor="middle">Finance and markets</text>
<text x="650" y="124" class="s" text-anchor="middle">Shopping</text><text x="650" y="146" class="s" text-anchor="middle">Health (Oura, phone data, openFDA)</text>
<text x="650" y="168" class="s" text-anchor="middle">Home (Nest)</text><text x="650" y="190" class="s" text-anchor="middle">Calls (Plivo, Deepgram, ElevenLabs)</text>
<text x="650" y="212" class="s" text-anchor="middle">Search</text><text x="650" y="234" class="s" text-anchor="middle">News and trends</text>
<text x="650" y="256" class="s" text-anchor="middle">Weather</text><text x="650" y="278" class="s" text-anchor="middle">AI models</text>
<text x="650" y="310" class="s" text-anchor="middle">each: public or personal</text>
<path class="flow" d="M140,175 H180" marker-end="url(#ah)"/>
<path class="flow" d="M510,175 H550" marker-end="url(#ah)"/>
</svg><figcaption>Figure 3. Buddy Connect: one catalog, one call path, one status. Adding a service is one catalog entry; it then
appears in status, cost reports and the app's Connections screen.</figcaption></figure>
"""

LINK_SVG = """
<figure class="dia" id="fig-link"><svg viewBox="0 0 760 250" role="img" aria-labelledby="lt">
<title id="lt">Connecting your own account: the app asks the server for a sign-in link with a one-time state; the browser
signs in at the provider; the provider sends the browser back to the server's private page, which checks the state and
swaps the code for tokens kept encrypted.</title>
<g class="s" text-anchor="middle">
<rect x="10" y="20" width="120" height="40" rx="10" class="box"/><text x="70" y="45" class="t">Buddy app</text>
<rect x="230" y="20" width="140" height="40" rx="10" class="mod"/><text x="300" y="45" class="t">Your server</text>
<rect x="470" y="20" width="140" height="40" rx="10" class="ext"/><text x="540" y="45" class="t">Provider sign-in</text>
<rect x="640" y="20" width="110" height="40" rx="10" class="box"/><text x="695" y="45" class="t">Browser</text>
</g>
<path class="line" d="M70,60 V240 M300,60 V240 M540,60 V240 M695,60 V240" stroke-dasharray="2 5"/>
<path class="flow" d="M70,85 H300" marker-end="url(#ah)"/><text x="185" y="80" class="s" text-anchor="middle">1. Connect (with your token)</text>
<path class="flow" d="M300,112 H70" marker-end="url(#ah)"/><text x="185" y="107" class="s" text-anchor="middle">2. link + one-time state (15 min)</text>
<path class="flow" d="M70,140 H695" marker-end="url(#ah)"/><text x="420" y="135" class="s" text-anchor="middle">3. opens the sign-in page</text>
<path class="flow pub" d="M695,165 H540" marker-end="url(#ao)"/><text x="618" y="160" class="s" text-anchor="middle">4. you sign in, allow</text>
<path class="flow" d="M540,192 H300" marker-end="url(#ah)"/><text x="420" y="187" class="s" text-anchor="middle">5. back to the server's private page with a code</text>
<path class="flow" d="M300,222 H540" marker-end="url(#ah)"/><text x="420" y="217" class="s" text-anchor="middle">6. state checked; code swapped for tokens (encrypted)</text>
</svg><figcaption>Figure 4. "Connect your own account" (OAuth), used by the Oura Ring and by banks through Plaid. Passwords are
typed only on the provider's page; the code never passes through anyone else's site; the state works once.</figcaption></figure>
"""

CALLS_SVG = """
<figure class="dia" id="fig-calls"><svg viewBox="0 0 760 250" role="img" aria-labelledby="pt">
<title id="pt">Phone agent: the caller's audio goes through the phone network and Plivo, over a signed stream to the
server, through voice activity detection, a cost gate, Deepgram, Claude and ElevenLabs, and back.</title>
<rect x="10" y="90" width="90" height="60" rx="12" class="box"/><text x="55" y="116" class="t" text-anchor="middle">Caller</text>
<text x="55" y="133" class="s" text-anchor="middle">or business</text>
<rect x="125" y="90" width="90" height="60" rx="12" class="ext"/><text x="170" y="116" class="t" text-anchor="middle">Plivo</text>
<text x="170" y="133" class="s" text-anchor="middle">phone line</text>
<rect x="232" y="100" width="22" height="40" rx="6" class="sec"/>
<rect x="270" y="30" width="480" height="190" rx="16" class="srv"/><text x="510" y="52" class="w" text-anchor="middle">PHONE AGENT ON YOUR SERVER</text>
<rect x="285" y="70" width="80" height="52" rx="8" class="mod"/><text x="325" y="92" class="t" text-anchor="middle">Who's</text><text x="325" y="108" class="t" text-anchor="middle">talking</text>
<rect x="380" y="70" width="80" height="52" rx="8" class="mod"/><text x="420" y="92" class="t" text-anchor="middle">Cost</text><text x="420" y="108" class="t" text-anchor="middle">gate</text>
<rect x="475" y="70" width="80" height="52" rx="8" class="ext"/><text x="515" y="92" class="t" text-anchor="middle">Deepgram</text><text x="515" y="108" class="s" text-anchor="middle">hears</text>
<rect x="570" y="70" width="80" height="52" rx="8" class="ext"/><text x="610" y="92" class="t" text-anchor="middle">Claude</text><text x="610" y="108" class="s" text-anchor="middle">decides</text>
<rect x="665" y="70" width="75" height="52" rx="8" class="ext"/><text x="702" y="92" class="t" text-anchor="middle">Eleven</text><text x="702" y="108" class="t" text-anchor="middle">Labs</text>
<rect x="285" y="150" width="455" height="54" rx="8" class="mod"/><text x="512" y="172" class="s" text-anchor="middle"><tspan class="t" font-size="12">Rules </tspan>honest it's an AI if asked; shares only allowed facts; never pays or cancels;</text>
<text x="512" y="190" class="s" text-anchor="middle">never gives card numbers, SSN or passwords; no recording or transcript; daily limits</text>
<path class="flow" d="M100,120 H125" marker-end="url(#ah)"/>
<path class="flow" d="M215,120 H232" marker-end="url(#ah)"/><path class="flow" d="M254,120 H285" marker-end="url(#ah)"/>
<path class="flow" d="M365,96 H380" marker-end="url(#ah)"/><path class="flow" d="M460,96 H475" marker-end="url(#ah)"/>
<path class="flow" d="M555,96 H570" marker-end="url(#ah)"/><path class="flow" d="M650,96 H665" marker-end="url(#ah)"/>
<path class="flow" d="M702,70 V60 H243 V100" marker-end="url(#ah)"/>
<text x="470" y="238" class="s" text-anchor="middle">Audio reaches Deepgram only while someone is talking; replies stream sentence by sentence</text>
</svg><figcaption>Figure 5. Calls. The red gate is the one public entry: each request needs the call's own random code and the
provider's signature. Inbound screening answers missed calls the same way.</figcaption></figure>
"""

SECURITY_SVG = """
<figure class="dia" id="fig-security"><svg viewBox="0 0 760 420" role="img" aria-labelledby="st">
<title id="st">Security layers, outside in: network, identity, application, secrets, data, AI boundary, actions.</title>
<rect x="10" y="10" width="740" height="400" rx="22" class="net"/>
<text x="30" y="36" class="w">1  NETWORK   private network only; no open ports; one signed public path for call audio</text>
<rect x="40" y="50" width="680" height="350" rx="20" fill="#f2f8f6" stroke="#1f6f5c"/>
<text x="60" y="76" class="w">2  IDENTITY   owner token; lockout after repeated failures; network login logged</text>
<rect x="70" y="90" width="620" height="300" rx="18" fill="#fff" stroke="#1f6f5c"/>
<text x="90" y="116" class="w">3  APPLICATION   strict content policy and headers; access log without bodies or query strings</text>
<rect x="100" y="130" width="560" height="250" rx="16" fill="#fbfaf7" stroke="#1f6f5c"/>
<text x="120" y="156" class="w">4  SECRETS   hidden prompts; sealed with the machine's own key; never in code, logs or chat</text>
<rect x="130" y="170" width="500" height="200" rx="14" fill="#fff" stroke="#1f6f5c"/>
<text x="150" y="196" class="w">5  DATA   AES-256 encrypted database; encrypted backups; retention limits</text>
<rect x="160" y="210" width="440" height="150" rx="12" class="sec"/>
<text x="180" y="236" class="w" fill="#b3261e">6  AI BOUNDARY   personal data only to Claude; masking; free models get public questions</text>
<rect x="190" y="250" width="380" height="100" rx="12" fill="#fff" stroke="#b3261e"/>
<text x="380" y="285" class="t" text-anchor="middle">7  ACTIONS</text>
<text x="380" y="305" class="s" text-anchor="middle">Nothing is paid, booked, sent, submitted, cancelled</text>
<text x="380" y="321" class="s" text-anchor="middle">or called without the owner's yes; budgets cap spend;</text>
<text x="380" y="337" class="s" text-anchor="middle">financial details are never filled in without asking</text>
</svg><figcaption>Figure 6. Defence in depth, outside in. Each ring holds even if the one outside it fails.</figcaption></figure>
"""

ARCH_BODY = ARCH_STYLE + """
<h1 id="summary">Buddy: end-to-end architecture</h1>
<p class="lead">How Buddy works, from the phone in your hand to every outside service, and how it keeps your data
safe. Written for a technical review: what each component does, how a request flows, how connectors work, and the
security model with its known gaps.</p>
<p><span class="pill warn">Draft for review</span> Updated """ + "{UPDATED}" + """.</p>

<h2>Summary</h2>
<ul class="ticks">
<li><strong>One private server per owner.</strong> Buddy runs on the owner's own small cloud server, reachable only over
an encrypted private network. There is no shared multi-user backend holding everyone's data.</li>
<li><strong>Claude does the thinking on anything personal.</strong> A router sends each turn to the cheapest model that does
it well; free models only ever see general questions with nothing about the owner.</li>
<li><strong>One gateway to the outside world.</strong> Buddy Connect is the single path to every outside service, with
keys, privacy rules, limits, logging and cost in one place.</li>
<li><strong>Nothing happens without a yes.</strong> Buddy informs, suggests and prepares; paying, booking, sending,
submitting, cancelling and calling wait for the owner's approval.</li>
<li><strong>Defence in depth.</strong> Private network, owner token with lockout, sealed secrets, an AES-256 encrypted
database, encrypted backups, an AI boundary and an action boundary.</li>
</ul>

<h2 id="principles">Design principles</h2>
<div class="table-wrap"><table>
<tr><th>Principle</th><th>What it means in practice</th></tr>
<tr><td>Owner-owned</td><td>The owner's data lives on the owner's server, encrypted; deleting it there deletes it.</td></tr>
<tr><td>Private by default</td><td>No public web surface for the app; only live call audio has a public, signed path.</td></tr>
<tr><td>Least data out</td><td>Each outside service gets only what its job needs: a drug name to openFDA, never who asked.</td></tr>
<tr><td>Read before act</td><td>Connectors read. Changes (thermostat, calendar add, calls) need an explicit yes each time.</td></tr>
<tr><td>Explain itself</td><td>Every service shows whether it works, why not, what it costs and what it may see.</td></tr>
<tr><td>Cheap by design</td><td>Model routing, caching and gates (for example audio only while someone talks) keep cost low.</td></tr>
</table></div>

<h2 id="overview">System overview</h2>
""" + OVERVIEW_SVG + """
<h2 id="journey">A request, end to end</h2>
""" + JOURNEY_SVG + """
<ol>
<li><strong>Ask.</strong> The app records a short utterance or takes text and sends it over the private network.</li>
<li><strong>Voice to text.</strong> Whisper on the owner's server. In Talk mode, a fast hosted Whisper may be used for
speed; listen-in audio (other people speaking) always stays on the server.</li>
<li><strong>Front door.</strong> The owner token is checked; repeated failures lock the address out; the access is logged
(who, when, path, result) without the message, body or query string.</li>
<li><strong>Router.</strong> Rules, not an extra model call, pick the model: small talk can go to a free model; anything
personal, or any doubt, goes to Claude (a quick model for short turns, a stronger one for planning, the strongest
for hard questions).</li>
<li><strong>Tools.</strong> The model calls tools: memory (bills, balances, calendar), Buddy Connect services, the
sandbox. Tools that change something ask the owner first.</li>
<li><strong>Answer.</strong> Streamed back over the live link with cards; the cost of the turn is recorded.</li>
<li><strong>Hear it.</strong> The app speaks and shows it.</li>
</ol>

<h2 id="components">Components</h2>
<div class="table-wrap"><table>
<tr><th>Component</th><th>What it does</th><th>Security notes</th></tr>
<tr><td>Buddy app (Android)</td><td>Chat and voice; phone bridge (notifications, replies, calendar, contacts with consent,
Health Connect, location when shared); Connections screen; alerts.</td><td>Talks only to the owner's server over the
private network with the owner token. Each phone permission is opt-in.</td></tr>
<tr><td>Web pages</td><td>Money, cards, investments, news and reports views served by the server.</td><td>Same network and
token; strict content policy.</td></tr>
<tr><td>Front door</td><td>The HTTP API and the live link; admin endpoints for the app.</td><td>Token check, lockout,
access log, security headers.</td></tr>
<tr><td>Brain</td><td>The agent: system prompt, tools, model routing, privacy guard, step-up to a stronger model.</td>
<td>Personal tools are never offered to free models; masking of account numbers and IDs.</td></tr>
<tr><td>Memory</td><td>Facts, goals, reminders, tasks, money, health, location history, conversations.</td><td>One
database encrypted with AES-256 (SQLCipher); per-type retention.</td></tr>
<tr><td>Proactive engine</td><td>Background loop: briefings, reminders, bills, travel and transit alerts, packages,
health-plan nudges, routine learning, nightly checks.</td><td>Quiet hours; holds non-urgent messages while driving or
in meetings; repeat guard.</td></tr>
<tr><td>Buddy Connect</td><td>The gateway to every outside service (Figure 3).</td><td>Keys stay on the server; privacy
class enforced per call; logs redact account ids.</td></tr>
<tr><td>Connectors hub</td><td>Calendar (private iCal link or the phone), email (read-only), phone data; each with health
and a kill switch.</td><td>Read-only; app content is treated as information, never as instructions.</td></tr>
<tr><td>Phone agent</td><td>Places calls for the owner after a yes; answers missed calls as a secretary (Figure 5).</td>
<td>Signed public path; strict sharing rules; no recording; limits.</td></tr>
<tr><td>Code sandbox</td><td>Runs code and data tools in throwaway containers.</td><td>Buddy never runs a command itself;
isolated from the database and secrets.</td></tr>
<tr><td>Self-improve</td><td>Turns problems into GitHub issues for Claude Code; changes come back as reviewed commits,
tested in CI.</td><td>Self-deploy refuses changes to protected areas (security, budgets, deploy scripts).</td></tr>
<tr><td>Operations</td><td>Nightly encrypted backups, health and security checks, key-age reminders, updates.</td>
<td>Backups encrypted to a key kept off the server.</td></tr>
</table></div>

<h2 id="connect">Buddy Connect: connectors</h2>
""" + CONNECT_SVG + """
<p>Every outside service is one catalog entry: its addresses (with backups), how it signs in, what it costs, its
per-minute limit, its group, and its privacy class. <strong>Public</strong> services receive only general data (a
city, a ticker, a drug name). <strong>Personal</strong> services may receive the owner's data because that is their job
(the bank aggregator, the owner's ring, the owner's thermostat); a call that carries personal data to a public
service is refused by the gateway itself.</p>
<div class="table-wrap"><table>
<tr><th>Sign-in kind</th><th>Used for</th><th>Where the secret lives</th></tr>
<tr><td>None</td><td>Open public data (weather, openFDA, Open Food Facts)</td><td>No secret</td></tr>
<tr><td>Key in a header or address</td><td>Search, markets, call providers</td><td>Server settings, sealed</td></tr>
<tr><td>Username and password for a token</td><td>Some transit feeds</td><td>Server settings; token reused</td></tr>
<tr><td>OAuth, connect your own account</td><td>Oura Ring, Google Nest, banks through Plaid</td><td>App id on the server;
the owner's tokens in the encrypted database, renewed automatically</td></tr>
<tr><td>Phone</td><td>Health Connect, contacts, calendar</td><td>Read on the phone with permission; sent only to the
owner's server</td></tr>
</table></div>
""" + LINK_SVG + """
<h2 id="calls">Phone agent</h2>
""" + CALLS_SVG + """
<p>Outbound calls start only after the owner approves the plan (who, why, what may be shared). Missed calls forwarded
by the carrier are answered like a secretary: known callers are greeted by name and may hear general availability
("in a meeting until three"), never a location; scams are ended; the owner gets a short summary. Call audio is
streamed, never recorded; only the outcome summary is kept (180 days).</p>

<h2 id="proactive">Proactive engine</h2>
<div class="table-wrap"><table>
<tr><th>When</th><th>What</th></tr>
<tr><td>Morning</td><td>Briefing: calendar, tasks, bills, email worth a look, weather, recovery from the ring, one goal</td></tr>
<tr><td>Through the day</td><td>Reminders, bill and card due dates, transit and flight alerts, packages, health-plan nudges</td></tr>
<tr><td>Evening</td><td>Wrap-up; steps gap; wind-down before bedtime target</td></tr>
<tr><td>Night</td><td>Routine learning, backups, health and security checks, data pruning by retention</td></tr>
</table></div>

<h2 id="data">Data and retention</h2>
<div class="table-wrap"><table>
<tr><th>Data</th><th>Where</th><th>Kept</th></tr>
<tr><td>Conversations, facts, goals, tasks</td><td>Encrypted database</td><td>Until the owner deletes them</td></tr>
<tr><td>Bank transactions and balances</td><td>Encrypted database (via Plaid, read-only)</td><td>Until the bank is disconnected</td></tr>
<tr><td>Health (phone, Oura), health plan logs and meals</td><td>Encrypted database</td><td>90 days</td></tr>
<tr><td>Location history</td><td>Encrypted database</td><td>60 days</td></tr>
<tr><td>Call outcome summaries</td><td>Encrypted database</td><td>180 days; no audio or transcript</td></tr>
<tr><td>Access log</td><td>Separate encrypted database</td><td>400 days; no bodies</td></tr>
<tr><td>Backups</td><td>Server and optional off-site copy, encrypted to an off-server key</td><td>Last 14 nightly</td></tr>
</table></div>
<p>The privacy policy is the source of truth for every data type, provider and retention period.</p>

<h2 id="security">Security architecture</h2>
""" + SECURITY_SVG + """
<div class="table-wrap"><table>
<tr><th>Layer</th><th>Controls</th></tr>
<tr><td>1 Network</td><td>The server is reachable only on the owner's private WireGuard network (Tailscale), with an
access policy. No open inbound ports on the public internet. One exception: a public path limited to live call audio,
where every request needs that call's random code and the call provider's signature.</td></tr>
<tr><td>2 Identity</td><td>Owner token on every request; brute-force lockout per address; the private network's login is
recorded with each access. Multi-factor authentication on every vendor account.</td></tr>
<tr><td>3 Application</td><td>Strict Content Security Policy, frame and sniffing protections; access log of who, when,
path and result, never message bodies or query strings; sign-in codes and tokens redacted from logs.</td></tr>
<tr><td>4 Secrets</td><td>Entered only at hidden prompts on the server; sealed with the machine's own key (only the Buddy
service can unseal them, into memory); every change dated, with reminders to rotate old keys.</td></tr>
<tr><td>5 Data</td><td>AES-256 encryption at rest for the database and access log; uploaded documents encrypted on disk;
TLS 1.2+ in transit; nightly backups encrypted again to a key kept off the server; retention limits per data type.</td></tr>
<tr><td>6 AI boundary</td><td>Personal data goes only to Claude. Free models get general questions with nothing about
the owner; the gateway refuses personal data to public services. Account numbers and IDs are masked. Outside
content (emails, web pages, app data) is treated as information, never as instructions.</td></tr>
<tr><td>7 Actions</td><td>Every action with consequences waits for the owner's yes; budgets cap AI and service spend;
financial details are never entered into a form without asking.</td></tr>
<tr><td>Code and change</td><td>Code runs only in throwaway sandbox containers; self-made changes go through GitHub
review and CI; protected areas (security, budgets, deploy) can't be changed by Buddy itself.</td></tr>
</table></div>

<h2 id="threats">Threats and mitigations</h2>
<div class="table-wrap"><table>
<tr><th>Threat</th><th>Mitigation</th><th>Residual risk</th></tr>
<tr><td>Someone on the internet attacks the server</td><td>No public ports; private network only; signed call path</td>
<td><span class="pill">Low</span></td></tr>
<tr><td>Stolen or lost phone</td><td>Owner token revocable; private network device can be removed; no data stored only on
the phone</td><td><span class="pill warn">Medium</span> until the device is removed</td></tr>
<tr><td>Server disk or backup stolen</td><td>Encrypted database; sealed secrets; backups need an off-server key</td>
<td><span class="pill">Low</span></td></tr>
<tr><td>Prompt injection through an email or web page</td><td>Outside content treated as data; actions need a yes; tools
scoped</td><td><span class="pill warn">Medium</span>: an inherent AI risk, contained by the action boundary</td></tr>
<tr><td>Personal data leaking to a free model</td><td>Router rules; personal tools withheld; gateway privacy check</td>
<td><span class="pill">Low</span></td></tr>
<tr><td>A vendor key leaks</td><td>Keys only on the server; least-privilege scopes; rotation reminders; spend caps</td>
<td><span class="pill">Low</span></td></tr>
<tr><td>Abuse of the phone agent</td><td>Approved calls only; allowed facts only; daily and per-call limits; honest AI
disclosure</td><td><span class="pill">Low</span></td></tr>
<tr><td>Bad self-made code change</td><td>Review, CI, protected areas, sandbox</td><td><span class="pill">Low</span></td></tr>
</table></div>

<h2 id="ops">Operations</h2>
<ul class="ticks">
<li>Updates: pull, update script, service restart; the app is built and tested in CI.</li>
<li>Backups: nightly, encrypted, last 14 kept, optional off-site copy; restore needs only the off-server key.</li>
<li>Checks: daily health check of Buddy itself (errors, latency, cost), weekly security check, key-age reminders.</li>
<li>Cost: every model and service call is costed; budgets and alerts; monthly totals per service in the app.</li>
</ul>

<h2 id="gaps">Known gaps and next steps</h2>
<div class="table-wrap"><table>
<tr><th>Gap</th><th>Plan</th></tr>
<tr><td>Single server per owner (no failover)</td><td>Acceptable for a personal assistant; restore from backup in under an
hour. Hot standby later if needed.</td></tr>
<tr><td>Independent penetration test</td><td><span class="pill warn">In progress</span></td></tr>
<tr><td>SOC 2 Type I / II</td><td><span class="pill warn">In progress</span>; not claimed until a CPA firm reports</td></tr>
<tr><td>Data processing agreements with every vendor</td><td><span class="pill warn">In progress</span></td></tr>
<tr><td>Multi-user operation</td><td>Each user gets their own server and their own connected accounts ("connect your
own account" is already per user).</td></tr>
</table></div>

<h2 id="review">Questions for the review</h2>
<ol>
<li>Is one server per owner the right isolation boundary as Buddy grows, or a shared backend with per-user encryption?</li>
<li>Is the one public path for call audio acceptable, or should calls move to a separate, isolated relay?</li>
<li>Which vendors need a signed DPA first: Plaid, Anthropic, the call providers?</li>
<li>Is the AI boundary (Claude only for personal data) the right line, and how should it be audited?</li>
<li>What should the penetration test scope include: the private network, the call path, the app, the backups?</li>
</ol>
"""

docs_page("architecture.html", f"Architecture · Buddy · {CO}", ARCH_BODY.replace("{UPDATED}", DOCS_UPDATED),
          side=ARCH_SIDE)
