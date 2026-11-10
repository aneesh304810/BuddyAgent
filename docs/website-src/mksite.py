import sys
CO = "Buddy Labs"
EMAIL = "hello@buddylabs.io"
PRIVACY_EMAIL = "privacy@buddylabs.io"
SECURITY_EMAIL = "security@buddylabs.io"
UPDATED = "November 4, 2026"
CO_URL = CO.replace(" ", "%20")
SITE = "https://buddylabs.io"
CUR = ' aria-current="page"'

def page(file, title, desc, body, current=""):
    nav = [("index.html", "Home"), ("connect.html", "Buddy Connect"), ("trust.html", "Trust Center"),
           ("security.html", "Security"), ("privacy.html", "Privacy"), ("contact.html", "Contact")]
    links = "".join(f'<a href="{h}"{CUR if h == current else ""}>{t}</a>' for h, t in nav)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light only">
<meta name="theme-color" content="#ffffff">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{SITE}/{'' if file == 'index.html' else file.replace('.html', '')}">
<meta property="og:url" content="{SITE}/{'' if file == 'index.html' else file.replace('.html', '')}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{CO}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{SITE}/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="styles.css">
</head>
<body>
<header class="site"><div class="wrap">
  <a class="logo" href="index.html"><span class="mark" aria-hidden="true">B</span><span>{CO}</span></a>
  <nav class="site" aria-label="Main">{links}</nav>
</div></header>
<main>
{body}
</main>
<script src="waitlist.js" defer></script>
<footer class="site"><div class="wrap">
  <span>© 2026 {CO}. All rights reserved. Buddy™ and "1,000 apps. One Buddy."™ are trademarks of {CO}.</span>
  <span><a href="trust.html">Trust Center</a> · <a href="privacy.html">Privacy</a> · <a href="terms.html">Terms</a> · <a href="security.html">Security</a> · <a href="subprocessors.html">Subprocessors</a> · <a href="vulnerability-disclosure.html">Report a vulnerability</a> · <a href="contact.html">Contact</a></span>
</div></footer>
</body>
</html>
"""
    open(file, "w").write(html)

exec(open(sys.argv[1]).read())
