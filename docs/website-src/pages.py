import os
import sys
page("index.html", f"Buddy: 1,000 apps, one Buddy · {CO}", "One app to rule them all: a personal assistant who knows you and organizes, manages and tailors everything around your style. Coming soon.", f"""
<section class="hero teaser"><div class="wrap">
  <span class="eyebrow">Coming soon</span>
  <h1>1,000 apps.<br><span class="accent">One Buddy.</span></h1>
  <p class="lead"><strong>One app to rule them all.</strong> A personal assistant who actually knows you, and organizes, manages and tailors everything around <em>your</em> style.</p>
  <div class="actions"><a class="btn primary" href="#join">Get early access</a><a class="btn" href="#familiar">Why Buddy?</a></div>
</div></section>

<section class="block" id="familiar"><div class="wrap">
  <h2>Sound familiar?</h2>
  <div class="grid quotes">
    <div class="card quote"><p>"Wait, I'm still paying for that?"</p></div>
    <div class="card quote"><p>"That perk expired <em>last week</em>?"</p></div>
    <div class="card quote"><p>"I told myself I'd remember."</p></div>
    <div class="card quote"><p>"Of course it went on sale the day after I bought it."</p></div>
    <div class="card quote"><p>"Twelve tabs open, and I still can't decide."</p></div>
    <div class="card quote"><p>"Where did all my money go this month?"</p></div>
  </div>
  <p class="lead" style="margin-top:28px">Small things. They add up to real money, lost time and a lot of mental load.</p>
</div></section>

<section class="block"><div class="wrap">
  <h2>What if someone had your back?</h2>
  <p class="lead">Someone who knows what you like, remembers what you said, notices what's coming, gets things done in the background, and speaks up at the right moment, then lets <em>you</em> decide.</p>
  <div class="grid">
    <div class="card"><h3>It remembers.</h3><p>So you don't have to.</p></div>
    <div class="card"><h3>It notices.</h3><p>Before it costs you.</p></div>
    <div class="card"><h3>It gets you.</h3><p>And it gets better the longer you know each other.</p></div>
    <div class="card"><h3>It works while you don't.</h3><p>Research, tasks and errands, handled in the background. It reports back when they're done.</p></div>
  </div>
  <p class="lead" style="margin-top:28px"><strong>That's Buddy.</strong> And that's all we'll say for now.</p>
</div></section>

<section class="block" id="glimpse"><div class="wrap demo-wrap">
  <div>
    <h2>A glimpse of Buddy</h2>
    <p class="lead">Just talk to it, the way you'd talk to a friend who's great with money, plans and remembering things. It keeps an eye on your inbox as mail arrives, fills in forms for you to check, makes sure your card perks don't go to waste, shows where your money really goes, helps you understand people speaking another language, works on your tasks and research in the background, and when you need something new, it builds it for you. Want it to work differently? Just tell it, and it adapts to you.</p>
    <p><a class="btn" href="buddy-demo.mp4">Watch the demo video</a></p>
    <p class="fine">Illustrative demo. Names, cards and numbers are examples.</p>
  </div>
  <div class="phone" aria-label="Animated example chat with Buddy">
    <div class="screen">
      <div class="bar"><span class="mark" aria-hidden="true">B</span><span>Buddy<small>your personal assistant</small></span></div>
      <div class="chat" data-demo aria-live="off"></div>
    </div>
  </div>
</div></section>
<script src="demo.js" defer></script>

<section class="block" id="next"><div class="wrap">
  <span class="eyebrow">Also coming soon from {CO}</span>
  <h2>AI for restaurants, front to back.</h2>
  <p class="lead">The same idea, built for the people who feed us: less guessing, fewer mistakes, faster service.</p>
  <div class="grid wide">
    <div class="card"><h3>Buddy Serve</h3><p>An AI point of sale your team wears, not one they walk back to. Smart glasses and a headset: orders go in hands-free, and nothing gets lost between the table and the kitchen.</p><p style="margin-top:14px"><a href="buddy-serve.mp4">Watch the teaser (35 s)</a></p></div>
    <div class="card"><h3>Buddy Connect</h3><p>One secure gateway between Buddy and the services you use, grouped by travel, finance, shopping, health and more. Every connection is checked, explained and kept private.</p><p style="margin-top:14px"><a href="connect.html">Learn more</a></p></div>
    <div class="card"><h3>Buddy Watch</h3><p>A second pair of eyes on every shift. Food safety, how fast guests are served, every order tracked from kitchen to table, and every pour at the bar.</p><p style="margin-top:14px"><a href="buddy-watch.mp4">Watch the teaser (35 s)</a></p></div>
  </div>
  <p class="fine">In development. Interested in an early pilot for your restaurant? Email <a href="mailto:{EMAIL}?subject=Restaurant%20pilot">{EMAIL}</a>.</p>
</div></section>

<section class="block cta" id="early"><div class="wrap">
  <h2>Be one of the first.</h2>
  <p class="lead">Early members get in first and help shape what Buddy becomes.</p>
  <form class="waitlist" id="join" action="/api/waitlist" method="post" novalidate>
    <div class="row">
      <label><span>Email</span><input type="email" name="email" required autocomplete="email" placeholder="you@example.com"></label>
      <label><span>First name <em>(optional)</em></span><input type="text" name="name" maxlength="80" autocomplete="given-name"></label>
    </div>
    <label><span>What would you love help with? <em>(optional)</em></span><textarea name="wants" rows="2" maxlength="500" placeholder="The one thing you'd hand off first"></textarea></label>
    <label class="hp" aria-hidden="true">Website<input type="text" name="website" tabindex="-1" autocomplete="off"></label>
    <label class="consent"><input type="checkbox" name="consent" value="yes" required> <span>Email me about early access. I can unsubscribe any time. See the <a href="privacy.html#waitlist">Privacy Policy</a>.</span></label>
    <button class="btn primary" type="submit">Join the waitlist</button>
    <p class="form-msg" role="status" aria-live="polite"></p>
  </form>
  <p class="fine">Two more {CO} products are in the works, for life beyond the phone.</p>
</div></section>

<section class="block" id="partners"><div class="wrap">
  <h2>Partnerships in progress</h2>
  <p class="lead">We're in the process of partnering with service providers across finance, travel and everyday life, so Buddy works with the services you already use, safely and with your consent.</p>
  <div class="actions"><a class="btn" href="mailto:{EMAIL}?subject=Partnership%20with%20{CO_URL}">Interested in partnering? Talk to us</a></div>
</div></section>

<section class="block" id="bank"><div class="wrap">
  <h2>Private by design</h2>
  <p>Buddy is built to protect personal and financial data from day one. Bank and card accounts connect through <a href="https://plaid.com/how-we-handle-data/" rel="noopener">Plaid</a>, read-only.</p>
  <ul class="ticks">
    <li>You sign in to your bank inside Plaid's own window. We never see or store your bank login.</li>
    <li>You choose which accounts to share, and give consent for each bank.</li>
    <li>Buddy reads account and transaction information only. It can't move money or make payments.</li>
    <li>You can disconnect a bank at any time in Buddy or at <a href="https://my.plaid.com" rel="noopener">my.plaid.com</a>. Access is revoked and that bank's data is deleted.</li>
  </ul>
  <div class="grid wide">
    <div class="card"><h3>Consent first</h3><p>Data is collected only after you connect an account or grant a permission, and you can withdraw it at any time.</p></div>
    <div class="card"><h3>No selling, no ads</h3><p>We never sell or rent data, use it for advertising, or use it to train AI models.</p></div>
    <div class="card"><h3>Your data, your key</h3><p>Your data is encrypted with its own key, and your backups are locked with a key only you hold. No one at {CO} looks at your data unless you ask us to or the law requires it.</p></div>
    <div class="card"><h3>Protected</h3><p>Encrypted at rest and in transit, multi-factor authentication, and role-based, least-privilege access. <a href="security.html">Security</a></p></div>
    <div class="card"><h3>Kept only as needed</h3><p>Defined retention periods, automatic deletion, and deletion on request. <a href="privacy.html">Privacy Policy</a></p></div>
  </div>
  <p class="fine">Buddy gives information and suggestions only; it never moves money or changes your accounts. Plaid is a trademark of Plaid Inc. {CO} is not affiliated with or endorsed by Plaid.</p>
</div></section>
""", "index.html")

page("privacy.html", f"Privacy Policy · {CO}", f"How {CO} collects, uses, protects and deletes personal data.", f"""
<div class="wrap doc">
  <h1>Privacy Policy</h1>
  <p class="updated">Last updated {UPDATED}</p>
  <p>This policy explains how {CO} handles personal data in Buddy: what we collect, why, who processes it, how long we keep it and your choices. Buddy is in private testing ahead of launch.</p>

  <h2>What we never do</h2>
  <ul class="ticks">
    <li>We never sell or rent personal data, or share it for marketing or advertising.</li>
    <li>We never use your data to train AI models or to make credit decisions.</li>
    <li>We never collect your bank login, and Buddy can't move money or make payments.</li>
  </ul>

  <h2>Data we collect</h2>
  <div class="table-wrap"><table>
    <tr><th>Category</th><th>Source</th></tr>
    <tr><td>Financial account data: account names and types, balances, transactions, and card details such as due dates, minimum payments and limits</td><td>Plaid, from the accounts you choose to share (read-only)</td></tr>
    <tr><td>Accounts you connect, such as email and calendar</td><td>The services you connect, with your permission</td></tr>
    <tr><td>Device data, such as notifications, approximate location and microphone input</td><td>Your phone, only with permissions you grant</td></tr>
    <tr><td>Content you share, such as documents and photos</td><td>You</td></tr>
    <tr><td>Preferences, goals and information you give us</td><td>You</td></tr>
  </table></div>

  <h2 id="waitlist">Waitlist and this website</h2>
  <p>If you join the waitlist, we keep your email address, your first name and anything you write in the form if you give them, the date, and your country (from our hosting provider). We use them only to email you about early access and launch, and to understand what people want help with. We don't store your IP address. You can unsubscribe or ask us to delete your signup at any time by emailing <a href="mailto:{PRIVACY_EMAIL}?subject=Remove%20me%20from%20the%20waitlist">{PRIVACY_EMAIL}</a>. This website uses no cookies, analytics or trackers.</p>

  <h2>Consent</h2>
  <p>You consent before any data is collected: in Plaid Link for each bank, through your phone's permission screens, and when you connect other accounts. You can withdraw consent at any time by disconnecting the account or turning off the permission.</p>

  <h2>How we use data</h2>
  <p>Only to provide Buddy's features to you: insights into your own spending, bills and card due dates, reminders, and answers to your questions. We don't use it for any other purpose.</p>

  <h2>How we protect it</h2>
  <p>Data is encrypted at rest and in transit, stored on systems that aren't exposed to the public internet, and protected by multi-factor authentication and role-based access. See <a href="security.html">Security</a>.</p>

  <h2>Service providers</h2>
  <p>We use a small number of service providers, each bound by its own privacy and security terms and given only the data its job needs:</p>
  <ul class="ticks">
    <li><strong>Plaid</strong>, to connect your bank accounts. See <a href="https://plaid.com/legal/#end-user-privacy-policy" rel="noopener">Plaid's End User Privacy Policy</a>.</li>
    <li>AI service providers, to understand requests and write answers. Personal data goes only to providers whose terms don't allow training on it.</li>
    <li>Cloud hosting and secure networking providers, including the provider that hosts this website and stores waitlist signups.</li>
    <li>Search and pricing providers, which receive general queries only, never your personal or financial data.</li>
  </ul>
  <p>None receives data for its own marketing or advertising. See the full <a href="subprocessors.html">list of subprocessors</a>.</p>

  <h2>How long we keep data</h2>
  <div class="table-wrap"><table>
    <tr><th>Data</th><th>Kept for</th></tr>
    <tr><td>Bank transactions and balances</td><td>24 months</td></tr>
    <tr><td>Bank connection (Plaid access)</td><td>Until you disconnect the bank. Access is then revoked and that bank's data deleted within 30 days.</td></tr>
    <tr><td>Connected email and calendar data</td><td>30 days or less</td></tr>
    <tr><td>Device data</td><td>7 days or less; location only the latest</td></tr>
    <tr><td>Voice audio</td><td>Not stored</td></tr>
    <tr><td>Content, preferences and information you give us</td><td>Until you delete it</td></tr>
    <tr><td>Security logs (time, network address, action and result; never message content)</td><td>400 days</td></tr>
    <tr><td>Voiceprint, if you turn on voice recognition</td><td>See the <a href="biometric-privacy.html">Biometric Information Privacy Policy</a></td></tr>
    <tr><td>Encrypted backups</td><td>14 days</td></tr>
    <tr><td>Waitlist signups</td><td>Until launch plus 12 months, or until you unsubscribe or ask us to delete them</td></tr>
  </table></div>

  <h2>Your rights and choices</h2>
  <ul class="ticks">
    <li>Disconnect a bank in Buddy or at <a href="https://my.plaid.com" rel="noopener">my.plaid.com</a>.</li>
    <li>Turn off any phone permission in your device settings.</li>
    <li>Ask us to access, correct, export or delete your data: email <a href="mailto:{PRIVACY_EMAIL}?subject=Privacy%20request">{PRIVACY_EMAIL}</a>. We act within 30 days.</li>
  </ul>

  <h2>Children</h2>
  <p>Buddy isn't for anyone under 18.</p>

  <h2>Changes and contact</h2>
  <p>We review this policy at least every 12 months, and whenever we start collecting a new kind of data. Changes are posted here with a new date. For questions, email <a href="mailto:{PRIVACY_EMAIL}">{PRIVACY_EMAIL}</a>.</p>
</div>
""", "privacy.html")

page("terms.html", f"Terms of Use · {CO}", f"Terms of use for {CO} and Buddy.", f"""
<div class="wrap doc">
  <h1>Terms of Use</h1>
  <p class="updated">Last updated {UPDATED}</p>
  <p>These terms cover this website and Buddy, made by {CO}. By using them you agree to these terms.</p>
  <h2>Access</h2>
  <p>Buddy is in private testing, and access is by invitation only. You must be 18 or older and use Buddy only for your own accounts and data.</p>
  <h2>Not financial advice</h2>
  <p>Buddy gives information and suggestions about your own finances. It isn't financial, tax, legal or investment advice. Check important decisions yourself.</p>
  <h2>Your accounts</h2>
  <p>Bank connections are read-only, through Plaid. Buddy doesn't move money, make payments or change your accounts.</p>
  <h2>Intellectual property</h2>
  <p>This website and everything on it are owned by {CO} and protected by copyright, trademark and other laws. That includes text, design, graphics, logos, the demo, videos and code. Buddy™ and "1,000 apps. One Buddy."™ are trademarks of {CO}. You may view and share links to this site, but you may not:</p>
  <ul class="ticks">
    <li>copy, reproduce, modify or republish its content, design or demo, except for brief quotes with credit;</li>
    <li>scrape, crawl or harvest it, or use it to train AI models, without our written permission;</li>
    <li>reverse engineer, decompile or try to extract the source code of Buddy or its services;</li>
    <li>use our names, logos or slogans in a way that suggests a connection with {CO}, or that could confuse people.</li>
  </ul>
  <h2>Your feedback</h2>
  <p>If you send us ideas or suggestions, including through the waitlist form, you agree we may use them freely, without any obligation to you. Please don't send anything you consider confidential.</p>
  <h2>Third-party services</h2>
  <p>Buddy works with third-party services, such as Plaid, under their own terms. We aren't responsible for their services. Their names belong to their owners, and mentioning them doesn't mean they endorse {CO}.</p>
  <h2>Products in development</h2>
  <p>Features, timing and availability may change.</p>
  <h2>Disclaimer and liability</h2>
  <p>Buddy is provided "as is", without warranties. To the extent the law allows, {CO} isn't liable for indirect or consequential losses arising from its use.</p>
  <h2>Privacy</h2>
  <p>Our <a href="privacy.html">Privacy Policy</a> explains how we handle data.</p>
  <h2>Changes and contact</h2>
  <p>We may update these terms and will post changes here. For questions, email <a href="mailto:{EMAIL}">{EMAIL}</a>.</p>
</div>
""")

page("contact.html", f"Contact · {CO}", f"Contact {CO}.", f"""
<div class="wrap doc">
  <h1>Contact</h1>
  <div class="grid">
    <div class="card"><h3>General</h3><p><a href="mailto:{EMAIL}">{EMAIL}</a></p></div>
    <div class="card"><h3>Privacy and data requests</h3><p><a href="mailto:{PRIVACY_EMAIL}?subject=Privacy%20request">Email us</a> with "Privacy request" in the subject. We act within 30 days.</p></div>
    <div class="card"><h3>Partnerships</h3><p>Service providers interested in working with us: <a href="mailto:{EMAIL}?subject=Partnership%20with%20{CO_URL}">get in touch</a>.</p></div>
    <div class="card"><h3>Security</h3><p><a href="mailto:{SECURITY_EMAIL}?subject=Security">Report an issue</a>. We aim to reply within 3 business days.</p></div>
  </div>
  <p style="margin-top:24px">{CO} · United States</p>
</div>
""", "contact.html")

page("404.html", f"Page not found · {CO}", "Page not found.", """
<div class="wrap doc"><h1>Page not found</h1><p><a href="index.html">Go to the home page</a></p></div>
""")

page("thanks.html", f"You're on the list · {CO}", "Thanks for joining the Buddy waitlist.", f"""
<div class="wrap doc" style="text-align:center">
  <span class="eyebrow">You're on the list</span>
  <h1>Thanks for joining.</h1>
  <p class="lead" style="margin:0 auto">We'll email you when early access opens. Until then: 1,000 apps, one Buddy. Coming soon.</p>
  <div class="actions" style="justify-content:center"><a class="btn" href="index.html">Back to the home page</a></div>
</div>
""")
# Trust Center pages for buddylabs.io (exec'd by mksite.py after pages_min.py).
# Every statement must be true of Buddy today, or say plainly that it is planned.
# No product features, hostnames, thresholds or internal tooling names: this is public.

STATUS_OK = '<span class="badge">In place</span>'
STATUS_PLAN = '<span class="badge plan">In progress</span>'

page("trust.html", f"Trust Center · {CO}", f"Security, privacy and compliance at {CO}: our program, controls, subprocessors, policies and how to report a vulnerability.", f"""
<div class="wrap doc wide">
  <span class="eyebrow">Trust Center</span>
  <h1>Built to be trusted with your money and your life.</h1>
  <p class="lead">Buddy handles personal and financial information, so security and privacy are designed in from the first line of code. This page summarises our program and links to the details.</p>
  <p class="updated">Last updated {UPDATED}</p>

  <div class="grid">
    <a class="card product" href="security.html"><h3>Security program</h3><p>Governance, encryption, access control, monitoring, incident response and business continuity.</p><span class="more">Read more →</span></a>
    <a class="card product" href="privacy.html"><h3>Privacy Policy</h3><p>What we collect, why, who processes it, how long we keep it and your choices.</p><span class="more">Read more →</span></a>
    <a class="card product" href="subprocessors.html"><h3>Subprocessors</h3><p>The service providers that process data for us, and what each receives.</p><span class="more">Read more →</span></a>
    <a class="card product" href="biometric-privacy.html"><h3>Biometric privacy</h3><p>Our written policy, retention schedule and consent for voice recognition.</p><span class="more">Read more →</span></a>
    <a class="card product" href="vulnerability-disclosure.html"><h3>Report a vulnerability</h3><p>Our disclosure policy, safe harbor for good-faith research and response times.</p><span class="more">Read more →</span></a>
    <a class="card product" href="contact.html"><h3>Contact</h3><p>Security: {SECURITY_EMAIL}<br>Privacy: {PRIVACY_EMAIL}</p><span class="more">Get in touch →</span></a>
  </div>

  <h2>Our commitments</h2>
  <ul class="ticks">
    <li>We never sell or rent personal data, never use it for advertising, and never use it to train AI models.</li>
    <li>We never see or store your bank password. Bank access is read-only: Buddy can't move money or make payments.</li>
    <li>Your data is encrypted at rest and in transit, on systems with no public entry points.</li>
    <li>Buddy acts on your behalf only with your explicit approval.</li>
    <li>We collect only what a feature needs and delete data on a published schedule.</li>
    <li>We tell you plainly what we have done and what is still in progress.</li>
  </ul>

  <h2>Compliance and frameworks</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Standard</th><th>Status</th><th>What it means</th></tr></thead>
    <tbody>
    <tr><td>SOC 2 (AICPA Trust Services Criteria)</td><td>{STATUS_PLAN}</td><td>We are preparing for a SOC 2 Type I examination by an independent CPA firm, followed by Type II. Our controls are mapped to the Security, Availability, Confidentiality and Privacy criteria, and we run a readiness assessment every month with evidence collected automatically. We will share the report under NDA once it is issued.</td></tr>
    <tr><td>FTC Safeguards Rule (GLBA, 16 CFR 314)</td><td>{STATUS_OK}</td><td>A written information security program with a designated Qualified Individual, risk assessment, safeguards, testing, training, service provider oversight and an incident response plan.</td></tr>
    <tr><td>NIST Cybersecurity Framework 2.0</td><td>{STATUS_OK}</td><td>Our program is organised around its six functions: Govern, Identify, Protect, Detect, Respond and Recover (see below).</td></tr>
    <tr><td>US state privacy laws (such as CCPA/CPRA)</td><td>{STATUS_OK}</td><td>Notice at collection, no sale or sharing for advertising, limits on sensitive data, and rights to know, delete and correct.</td></tr>
    <tr><td>Biometric privacy laws (Illinois BIPA, Texas, Washington)</td><td>{STATUS_OK}</td><td>A public written policy with a retention and destruction schedule, and written consent before any voiceprint is created.</td></tr>
    <tr><td>Payment card data</td><td>{STATUS_OK}</td><td>We never store full card numbers and don't process payments. Only a card's name and last four digits are kept.</td></tr>
    </tbody>
  </table></div>
  <p class="fine">{CO} does not hold a SOC 2 report yet. We describe our status as "preparing for SOC 2", never as "SOC 2 compliant" or "certified".</p>

  <h2>Our program at a glance (NIST CSF 2.0)</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Function</th><th>How we do it</th></tr></thead>
    <tbody>
    <tr><td>Govern</td><td>Written information security program and policies, a named security owner, yearly review and sign-off, risk-based priorities.</td></tr>
    <tr><td>Identify</td><td>Asset and data inventory, risk assessment and risk register, review of every service provider before use and yearly.</td></tr>
    <tr><td>Protect</td><td>Encryption at rest and in transit, multi-factor authentication, role-based least-privilege access, hardened and isolated systems, secure development practices, training.</td></tr>
    <tr><td>Detect</td><td>Access logging, failed sign-in detection with automatic lockout, scans for vulnerabilities, misconfigurations, exposed services and leaked credentials.</td></tr>
    <tr><td>Respond</td><td>A documented incident response plan with same-day containment and notification within legal deadlines.</td></tr>
    <tr><td>Recover</td><td>Encrypted nightly backups, a documented restore procedure, and systems that can be rebuilt from code.</td></tr>
    </tbody>
  </table></div>

  <h2>Assurance roadmap</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Activity</th><th>Status</th></tr></thead>
    <tbody>
    <tr><td>Automated security checks and patching, every week</td><td>{STATUS_OK}</td></tr>
    <tr><td>Code, dependency and secret scanning on every change</td><td>{STATUS_OK}</td></tr>
    <tr><td>Quarterly access reviews with records kept</td><td>{STATUS_OK}</td></tr>
    <tr><td>Monthly SOC 2 readiness assessment</td><td>{STATUS_OK}</td></tr>
    <tr><td>Independent penetration test (yearly, the first before public launch)</td><td>{STATUS_PLAN}</td></tr>
    <tr><td>Vulnerability assessment every 6 months</td><td>{STATUS_PLAN}</td></tr>
    <tr><td>SOC 2 Type I examination, then Type II</td><td>{STATUS_PLAN}</td></tr>
    <tr><td>Data processing agreements with every subprocessor</td><td>{STATUS_PLAN}</td></tr>
    </tbody>
  </table></div>

  <h2>Security reviews and documents</h2>
  <p>Partners and financial institutions can ask for our security documentation under a non-disclosure agreement, including a summary of our Information Security Program, our access control policy, our latest SOC 2 readiness assessment, and answers to standard questionnaires (such as SIG Lite or CAIQ). Email <a href="mailto:{SECURITY_EMAIL}?subject=Security%20review">{SECURITY_EMAIL}</a>.</p>
</div>
""", "trust.html")

page("security.html", f"Security · {CO}", f"How {CO} protects data: governance, encryption, access control, secure development, monitoring, incident response and business continuity.", f"""
<div class="wrap doc">
  <h1>Security</h1>
  <p class="updated">Last updated {UPDATED}</p>
  <p>{CO} protects personal and financial data with a written information security program designed to meet the FTC Safeguards Rule and mapped to the AICPA SOC 2 Trust Services Criteria and the NIST Cybersecurity Framework. This page describes how. See the <a href="trust.html">Trust Center</a> for our compliance status.</p>

  <h2>Governance and risk management</h2>
  <ul class="ticks">
    <li>A written Information Security Program, reviewed at least once a year and after any significant change or security event.</li>
    <li>A designated Qualified Individual who owns the program, approves access, oversees service providers and leads incident response.</li>
    <li>A documented risk assessment and risk register, updated at least yearly.</li>
    <li>Written policies for access control, data retention, biometric data, incident response and vulnerability disclosure.</li>
  </ul>

  <h2>Infrastructure and network security</h2>
  <ul class="ticks">
    <li>Hosted with a major cloud provider whose data centres hold independent SOC 2 Type II reports.</li>
    <li>No public entry points: our application servers can't be reached from the public internet. Access is only over an encrypted, device-authenticated private network.</li>
    <li>Firewalls deny all incoming traffic by default.</li>
    <li>Services run with least privilege under operating-system sandboxing: read-only system files, no elevated privileges, restricted system calls.</li>
    <li>Untrusted code runs only in isolated, disposable containers with no access to customer data, keys or internal networks.</li>
  </ul>

  <h2>Data protection and encryption</h2>
  <ul class="ticks">
    <li>Encryption at rest with AES-256 for databases and stored files.</li>
    <li>Encryption in transit with TLS 1.2 or higher for every connection.</li>
    <li>Keys and credentials are kept in encrypted, machine-bound storage, never in code, and are rotated regularly.</li>
    <li>Backups are encrypted with a key that is kept offline, separate from our systems.</li>
    <li>Sensitive values such as card and account numbers are masked before they're stored or processed. We never store full card numbers or bank passwords.</li>
    <li>Keys and tokens are filtered out of logs, and logs are scanned for anything that slips through.</li>
  </ul>

  <h2>Your data, your key</h2>
  <ul class="ticks">
    <li>Your data is encrypted with its own encryption key.</li>
    <li>Your backups are encrypted with a key that only you hold. It isn't stored on our systems.</li>
    <li>No one at {CO} looks at your data unless you ask us to (for example, for support) or the law requires it, and access to systems is restricted and logged.</li>
    <li>You can disconnect accounts and delete your data at any time.</li>
  </ul>

  <h2>Identity and access management</h2>
  <ul class="ticks">
    <li>A written access control policy with role-based, least-privilege access and no shared accounts.</li>
    <li>Centralised identity with multi-factor authentication for all administrative access. Server access uses keys only, never passwords.</li>
    <li>Only devices registered to our private network can reach our systems.</li>
    <li>Access reviews every quarter, with records kept.</li>
    <li>Automated removal of any access that isn't on the approved list, checked every day, and same-day offboarding.</li>
    <li>Repeated failed sign-ins automatically lock out the source.</li>
  </ul>

  <h2>Application security and development</h2>
  <ul class="ticks">
    <li>All changes go through version control and automated testing before release. Application releases are installed deliberately by an administrator; the application can't deploy changes to itself.</li>
    <li>Every change is scanned for vulnerable dependencies, unsafe code patterns and committed secrets. Dependencies are updated continuously.</li>
    <li>Web pages are served with strict security headers, including a Content Security Policy, HSTS and protection against framing.</li>
    <li>Download links are signed and expire.</li>
    <li>AI safeguards: content from emails, web pages and documents is treated as information, never as instructions. Actions such as sending, buying or booking need your explicit approval, enforced in code.</li>
  </ul>

  <h2>Vulnerability management</h2>
  <ul class="ticks">
    <li>Operating system security updates are applied automatically every day.</li>
    <li>A weekly automated check applies patches and reviews configuration, exposed services, credential age and backups. A patch that breaks service is rolled back automatically.</li>
    <li>Findings are fixed by severity: critical within 7 days, high within 30 days.</li>
    <li>Vulnerability assessments every 6 months and a yearly independent penetration test (planned; the first before public launch).</li>
    <li>Good-faith security research is welcome. See our <a href="vulnerability-disclosure.html">vulnerability disclosure policy</a>.</li>
  </ul>

  <h2>Logging and monitoring</h2>
  <ul class="ticks">
    <li>Access to our systems is logged: time, network address, the action and its result, never the content of your messages. Security logs are kept for 400 days.</li>
    <li>Automated monitoring for failed sign-ins, configuration changes, exposed services and leaked credentials.</li>
    <li>Daily health checks, with findings reviewed by the security owner.</li>
  </ul>

  <h2>Incident response</h2>
  <ul class="ticks">
    <li>A documented incident response plan: detect, contain the same day, assess, notify, recover and review.</li>
    <li>Notification to affected users, regulators and partners within the deadlines set by law, including the FTC Safeguards Rule.</li>
    <li>A written review after every incident, and incident records kept for at least 5 years.</li>
  </ul>

  <h2>Business continuity and disaster recovery</h2>
  <ul class="ticks">
    <li>Encrypted nightly backups, kept for 14 days.</li>
    <li>A documented restore procedure. Systems can be rebuilt from code onto a clean server.</li>
    <li>Capacity and service health are monitored continuously.</li>
  </ul>

  <h2>Service providers</h2>
  <ul class="ticks">
    <li>Every provider is reviewed before use and every year, and receives only the data its job needs.</li>
    <li>Providers that handle personal data must protect it under their terms, and AI providers may not train on it.</li>
    <li>See the <a href="subprocessors.html">list of subprocessors</a>.</li>
  </ul>

  <h2>People</h2>
  <ul class="ticks">
    <li>Access to systems and customer data is limited to the founder today.</li>
    <li>Anyone we give access to will sign confidentiality terms and complete security awareness training first, and again every year.</li>
    <li>Access is granted only through the access control policy and removed the same day someone leaves.</li>
  </ul>

  <h2>Reporting a security issue</h2>
  <p>Email <a href="mailto:{SECURITY_EMAIL}?subject=Security">{SECURITY_EMAIL}</a> with "Security" in the subject, or see our <a href="vulnerability-disclosure.html">vulnerability disclosure policy</a>. We aim to reply within 3 business days.</p>
</div>
""", "security.html")

page("subprocessors.html", f"Subprocessors · {CO}", f"The service providers that process data for {CO}, and what each receives.", f"""
<div class="wrap doc">
  <h1>Subprocessors</h1>
  <p class="updated">Last updated {UPDATED}</p>
  <p>These providers process data on our behalf. Each receives only what its job needs, is bound by its own privacy and security terms, and is reviewed before use and every year. None may use your data for its own marketing or advertising, and AI providers may not train on it.</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Provider</th><th>Purpose</th><th>Data it receives</th></tr></thead>
    <tbody>
    <tr><td>Plaid Inc.</td><td>Read-only bank connections</td><td>Bank data from the accounts you choose to share</td></tr>
    <tr><td>Anthropic, PBC</td><td>AI that understands requests and writes answers</td><td>The request and the data needed to answer it. Not used for training.</td></tr>
    <tr><td>DigitalOcean, LLC</td><td>Cloud hosting</td><td>Encrypted data at rest</td></tr>
    <tr><td>Tailscale Inc.</td><td>Encrypted private networking</td><td>Encrypted traffic it can't read; device names</td></tr>
    <tr><td>Cloudflare, Inc.</td><td>This website, waitlist and email routing</td><td>Waitlist signups and email sent to our addresses</td></tr>
    <tr><td>Speech-to-text provider</td><td>Turns your speech into text</td><td>Short voice clips, not kept by us afterwards</td></tr>
    <tr><td>Search and pricing data providers</td><td>Public web search and prices</td><td>General queries only, never your personal or financial data</td></tr>
    </tbody>
  </table></div>
  <p>We'll update this page before adding a provider that receives personal data. Questions: <a href="mailto:{PRIVACY_EMAIL}">{PRIVACY_EMAIL}</a>.</p>
  <p class="fine">Plaid is a trademark of Plaid Inc. Other names are trademarks of their owners. {CO} is not affiliated with or endorsed by these companies.</p>
</div>
""", "subprocessors.html")

page("vulnerability-disclosure.html", f"Vulnerability Disclosure Policy · {CO}", f"How to report a security vulnerability to {CO}, with safe harbor for good-faith research.", f"""
<div class="wrap doc">
  <h1>Vulnerability Disclosure Policy</h1>
  <p class="updated">Last updated {UPDATED}</p>
  <p>We welcome reports from security researchers and anyone who finds a weakness in our systems. We'll work with you to understand and fix it quickly.</p>

  <h2>How to report</h2>
  <p>Email <a href="mailto:{SECURITY_EMAIL}?subject=Security%20vulnerability">{SECURITY_EMAIL}</a> with "Security" in the subject. Please include:</p>
  <ul>
    <li>the affected address, page or component;</li>
    <li>steps to reproduce, and a proof of concept if you have one;</li>
    <li>the impact you believe it has;</li>
    <li>how we can reach you for follow-up.</li>
  </ul>

  <h2>What to expect</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Step</th><th>Our target</th></tr></thead>
    <tbody>
    <tr><td>Acknowledge your report</td><td>Within 3 business days</td></tr>
    <tr><td>Confirm the issue and its severity</td><td>Within 10 business days</td></tr>
    <tr><td>Fix a critical issue</td><td>Within 7 days of confirmation</td></tr>
    <tr><td>Fix a high-severity issue</td><td>Within 30 days of confirmation</td></tr>
    <tr><td>Keep you informed</td><td>Until it's resolved, and we'll tell you when it is</td></tr>
    </tbody>
  </table></div>

  <h2>Scope</h2>
  <p>In scope: buddylabs.io and its pages, forms and APIs, and the Buddy apps. Out of scope: services run by our providers (report those to them directly), physical attacks, social engineering of people, and denial-of-service testing.</p>

  <h2>Guidelines</h2>
  <ul>
    <li>Only test against accounts and data you own. Never access, change or delete other people's data.</li>
    <li>If you reach personal data, stop, don't keep it, and tell us right away.</li>
    <li>Don't degrade our services, run automated scans that generate heavy traffic, or send spam.</li>
    <li>Give us a reasonable time to fix the issue before telling anyone else about it.</li>
  </ul>

  <h2>Safe harbor</h2>
  <p>If you make a good-faith effort to follow this policy, we will consider your research authorized, we won't pursue or support legal action against you for it, and we'll work with you to understand and resolve the issue. If a third party takes legal action against you for activity that followed this policy, we'll make it known that your actions were authorized by us.</p>

  <h2>Recognition</h2>
  <p>We don't run a paid bug bounty yet. With your permission, we're happy to thank you publicly for a valid report.</p>
  <p class="fine">Machine-readable contact: <a href="/.well-known/security.txt">/.well-known/security.txt</a></p>
</div>
""", "vulnerability-disclosure.html")

page("biometric-privacy.html", f"Biometric Information Privacy Policy · {CO}", f"How {CO} collects, uses, stores and destroys biometric information, including our retention schedule.", f"""
<div class="wrap doc">
  <h1>Biometric Information Privacy Policy</h1>
  <p class="updated">Last updated {UPDATED}</p>
  <p>This policy explains how {CO} handles biometric information in Buddy, including our retention schedule and guidelines for permanently destroying it. It is written to meet the Illinois Biometric Information Privacy Act (740 ILCS 14) and similar laws in Texas, Washington, Colorado and California. Buddy is in private testing; this policy applies to everyone who uses it.</p>

  <h2>What we collect</h2>
  <p>The only biometric information Buddy collects is a <strong>voiceprint</strong>, and only from a user who turns on voice recognition. A voiceprint is a set of numbers that describes how your voice sounds, made from a few sentences you read once. We don't keep the recordings. We never create voiceprints of anyone else, and we don't collect face, fingerprint, iris or hand-geometry data.</p>

  <h2>Purpose, notice and consent</h2>
  <ul class="ticks">
    <li>The only purpose is to recognise your own voice, so Buddy responds to you and not to other people nearby.</li>
    <li>Voice recognition is optional and off by default. Buddy works fully without it.</li>
    <li>Before any voiceprint is created, we tell you in writing what is collected, why and for how long, and ask for your written consent. We keep a record of that consent.</li>
    <li>We'll ask for new consent before using a voiceprint for any other purpose. It is never used for advertising, profiling or decisions about you.</li>
  </ul>

  <h2>No sale or disclosure</h2>
  <p>We never sell, lease, trade or otherwise profit from biometric information. We never disclose it, unless you consent to a specific disclosure, it's needed to complete a transaction you asked for, or the law, a warrant or a subpoena requires it.</p>

  <h2>How we protect it</h2>
  <p>Voiceprints are created on our own systems, never sent to an AI provider or any other company, and stored encrypted with the same or stronger protection than our other sensitive data (see <a href="security.html">Security</a>).</p>

  <h2>Retention and destruction schedule</h2>
  <p>A voiceprint is permanently destroyed at the <strong>earliest</strong> of these events, and never kept longer than 3 years after your last interaction with Buddy:</p>
  <div class="table-wrap"><table>
    <thead><tr><th>Event</th><th>Destroyed within</th></tr></thead>
    <tbody>
    <tr><td>You turn voice recognition off, or ask Buddy to forget your voice</td><td>Immediately; encrypted backups expire within 14 days</td></tr>
    <tr><td>You withdraw consent or ask us to delete it</td><td>Immediately; encrypted backups expire within 14 days</td></tr>
    <tr><td>You delete your account</td><td>30 days</td></tr>
    <tr><td>Voice recognition unused for 12 months</td><td>30 days after the 12 months</td></tr>
    <tr><td>3 years after your last interaction with Buddy</td><td>On that date at the latest</td></tr>
    </tbody>
  </table></div>

  <h2>Your rights</h2>
  <p>You can see, withdraw and delete your voiceprint at any time in the app, or by emailing <a href="mailto:{PRIVACY_EMAIL}?subject=Biometric%20request">{PRIVACY_EMAIL}</a> with "Biometric request" in the subject. We act within 30 days.</p>
</div>
""", "biometric-privacy.html")

page("connect.html", f"Buddy Connect · {CO}", "Buddy Connect: one secure gateway between Buddy and the services you use, organised by travel, finance, shopping, health and more.", f"""
<section class="hero"><div class="wrap">
  <span class="eyebrow">Buddy Connect</span>
  <h1>Every service.<br><span class="accent">One secure gateway.</span></h1>
  <p class="lead">Buddy Connect is how Buddy, and apps we approve, reach the services in your life: travel, finance, shopping, health, news and more. One place to connect them, see that they work, and keep your data where it belongs.</p>
  <div class="actions"><a class="btn primary" href="docs/">API documentation</a><a class="btn" href="mailto:{EMAIL}?subject=Buddy%20Connect">Talk to us</a></div>
</div></section>

<section class="block"><div class="wrap">
  <h2>What it does</h2>
  <div class="grid">
    <div class="card"><h3>One way in</h3><p>Every outside service goes through the same gateway, so each one is handled the same careful way.</p></div>
    <div class="card"><h3>Organised by your life</h3><p>Travel and transit, finance and markets, shopping, health, search, news and weather, each in its own group.</p></div>
    <div class="card"><h3>Always explained</h3><p>See which connections work, which don't and why, in plain words, with a one-tap test.</p></div>
    <div class="card"><h3>Private by rule</h3><p>Each service is cleared for what it may see. Personal data only goes where it's allowed, and never to services that don't need it.</p></div>
    <div class="card"><h3>Costs in the open</h3><p>Every call is counted, so what each service costs is never a surprise.</p></div>
    <div class="card"><h3>Built to grow</h3><p>New services join in one step, and Buddy can use them right away.</p></div>
  </div>
  <p class="fine">Buddy Connect is in private testing. The API documentation is available to approved partners: <a href="mailto:{EMAIL}?subject=Buddy%20Connect%20access">ask for access</a>.</p>
</div></section>
""", "connect.html")

# Buddy Connect's API documentation (/docs/, behind the password login)
exec(open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "apidocs.py")).read())
