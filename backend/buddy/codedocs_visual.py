"""The technical documentation as one visual page: animated diagrams of how Buddy works, drawn from the code.

  - Architecture: the phone, the server, Claude, the free models, Buddy Connect and the database, with moving dots
    showing which way data flows (and a count of modules, tools, endpoints and tables read from the code).
  - Two flows, animated step by step: a chat message, and a phone call.
  - Buddy Connect: every service, grouped, coloured by whether it may see personal data.
  - Every server module: what it's for, and a small map of what it uses and what uses it.
  - Tools, endpoints, tables and settings, with a search box over the whole page.

One self-contained HTML file (inline SVG, CSS and a few lines of script; no outside libraries): it downloads to the
phone and opens offline in the browser. Every piece of text from the code is escaped.
"""

import re
from html import escape
from pathlib import Path

from . import codedocs

W, H = 760, 420


def _e(s) -> str:
    return escape(str(s), quote=True)


def _box(x, y, w, h, title, sub="", cls="box") -> str:
    return (f'<g class="{cls}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>'
            f'<text x="{x + w / 2}" y="{y + h / 2 - (6 if sub else -5)}" class="t">{_e(title)}</text>'
            + (f'<text x="{x + w / 2}" y="{y + h / 2 + 14}" class="s">{_e(sub)}</text>' if sub else "") + "</g>")


def _flow(path_id: str, d: str, dur: float = 2.4, delay: float = 0.0, back: bool = False) -> str:
    """A line with dots travelling along it (both ways when back=True)."""
    dots = "".join(
        f'<circle r="4.5" class="dot{" back" if b else ""}" visibility="hidden">'
        f'<set attributeName="visibility" to="visible" begin="{delay + i * dur / 3:.2f}s"/>'
        f'<animateMotion dur="{dur}s" begin="{delay + i * dur / 3:.2f}s"'
        f' repeatCount="indefinite" keyPoints="{"1;0" if b else "0;1"}" keyTimes="0;1" calcMode="linear">'
        f'<mpath href="#{path_id}"/></animateMotion></circle>'
        for b in ([False, True] if back else [False]) for i in range(3))
    return f'<path id="{path_id}" d="{d}" class="wire"/>{dots}'


def architecture(stats: dict) -> str:
    s = [f'<svg viewBox="0 0 {W} {H}" class="diagram" role="img" aria-label="How Buddy fits together">']
    s.append(_flow("a1", "M150,210 L300,210", back=True))
    s.append(_flow("a2", "M460,190 C540,120 560,80 600,70", 2.6, 0.3, True))
    s.append(_flow("a3", "M460,205 C540,190 560,175 600,170", 2.6, 0.6, True))
    s.append(_flow("a4", "M460,225 C540,250 560,265 600,270", 2.6, 0.9, True))
    s.append(_flow("a5", "M460,240 C540,320 560,355 600,365", 2.6, 1.2, True))
    s.append(_box(20, 160, 130, 100, "Phone app", "Android, Kotlin"))
    s.append(_box(300, 140, 160, 140, "Buddy server", f"{stats['modules']} modules", "box main"))
    s.append(f'<text x="380" y="300" class="s">{stats["tools"]} tools · {stats["endpoints"]} endpoints</text>')
    s.append(f'<text x="225" y="200" class="s">Tailscale</text>')
    s.append(_box(600, 40, 140, 60, "Claude", "the main brain"))
    s.append(_box(600, 140, 140, 60, "Free models", "Groq, Gemini, OpenRouter…"))
    s.append(_box(600, 240, 140, 60, "Buddy Connect", f"{stats['services']} outside services"))
    s.append(_box(600, 335, 140, 60, "Database", f"{stats['tables']} tables, encrypted"))
    s.append("</svg>")
    return "".join(s)


def steps(name: str, items: list[tuple[str, str]], seconds: float = 1.1) -> str:
    """Boxes in a row (wrapping), lit up one after another, with dots running between them."""
    per_row, bw, bh, gap = 3, 210, 64, 45
    rows = (len(items) + per_row - 1) // per_row
    h = rows * (bh + 60) + 20
    total = seconds * len(items)
    out = [f'<svg viewBox="0 0 {W} {h}" class="diagram" role="img" aria-label="{_e(name)}">']
    pos = []
    for i in range(len(items)):
        r, c = divmod(i, per_row)
        c = c if r % 2 == 0 else per_row - 1 - c  # snake, so the path never jumps back across
        pos.append((20 + c * (bw + gap), 20 + r * (bh + 60)))
    for i in range(len(items) - 1):
        (x1, y1), (x2, y2) = pos[i], pos[i + 1]
        a = (x1 + bw / 2, y1 + bh / 2)
        b = (x2 + bw / 2, y2 + bh / 2)
        out.append(_flow(f"{name[:3]}{i}", f"M{a[0]},{a[1]} L{b[0]},{b[1]}", seconds, i * seconds))
    for i, ((x, y), (title, sub)) in enumerate(zip(pos, items)):
        out.append(f'<g class="box step" style="animation-duration:{total:.1f}s;animation-delay:{i * seconds:.1f}s">'
                   f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="12"/>'
                   f'<text x="{x + bw / 2}" y="{y + 26}" class="t">{i + 1}. {_e(title)}</text>'
                   f'<text x="{x + bw / 2}" y="{y + 46}" class="s">{_e(sub)}</text></g>')
    out.append("</svg>")
    return "".join(out)


CONNECT_STEPS = [("A tool needs data", "e.g. train alerts, prices"), ("Find the service", "in the catalog"),
                 ("Privacy check", "personal data? only allowed services"), ("Rate limit", "calls per minute"),
                 ("Key added", "or a sign-in token, reused"), ("Main host", "then a backup if it's down"),
                 ("Retry once", "reads only, on a hiccup"), ("Logged", "time, cost, error → Insights")]
FREE_STEPS = [("General request", "translate, summarise, explain…"), ("Personal-data check", "emails, numbers, secrets, "
              "memory"), ("Best free model", "by what each is good at"), ("At its limit?", "the next one is tried"),
              ("Last resort", "Claude's quick model"), ("Logged", "speed and result → Insights")]

CHAT = [("You speak or type", "the phone app"), ("Over Tailscale", "WebSocket to the server"),
        ("Memory and context", "facts, recent chat, calendar"), ("Claude decides", "answer or use a tool"),
        ("Tools run", "calendar, money, travel, calls…"), ("Buddy Connect", "outside services, logged"),
        ("The reply", "spoken sentence by sentence"), ("Saved", "chat log and costs")]
CALL = [("Your yes", "Buddy asks first"), ("Plivo dials", "Buddy's number"), ("Audio streams", "to your server"),
        ("Silero hears", "who is talking"), ("Cost gate", "only speech goes on"), ("Deepgram", "speech to text"),
        ("Claude", "what to say, keys to press"), ("ElevenLabs", "the voice"), ("Outcome", "a summary for you")]


def connect_map() -> str:
    try:
        from . import gateway  # noqa: PLC0415
    except Exception:
        return ""
    groups = [(g, [n for n in gateway.CATALOG if gateway.group_of(n) == g]) for g in gateway.GROUPS + ["Other"]]
    groups = [(g, ns) for g, ns in groups if ns]
    cols, cw = 3, 240
    out, heights = [], []
    for i, (g, names) in enumerate(groups):
        c, r = i % cols, i // cols
        x = 10 + c * (cw + 10)
        y = sum(heights[k] for k in range(c, i, cols)) + 10 + (r * 10)
        h = 34 + 24 * len(names)
        heights.append(h + 10)
        out.append(f'<g class="group"><rect x="{x}" y="{y}" width="{cw}" height="{h}" rx="12"/>'
                   f'<text x="{x + 12}" y="{y + 22}" class="gt">{_e(g)}</text>')
        for j, n in enumerate(names):
            e = gateway.CATALOG[n]
            kind = "personal" if e.get("privacy") == "personal" else "public"
            out.append(f'<circle cx="{x + 18}" cy="{y + 40 + j * 24}" r="5" class="{kind}"/>'
                       f'<text x="{x + 30}" y="{y + 44 + j * 24}" class="svc">{_e(e.get("title", n))[:34]}</text>')
        out.append("</g>")
    total = max((sum(heights[k] for k in range(c, len(groups), cols)) for c in range(cols)), default=0) + 30
    return (f'<svg viewBox="0 0 {W} {total}" class="diagram" role="img" aria-label="Buddy Connect services">'
            + "".join(out) + "</svg>")


def neighbours(name: str, uses: list[str], used_by: list[str]) -> str:
    """One module in the middle; what it uses on the right, what uses it on the left."""
    left, right = used_by[:10], uses[:10]
    rows = max(len(left), len(right), 1)
    h = max(110, rows * 28 + 30)
    cy = h / 2
    out = [f'<svg viewBox="0 0 {W} {h}" class="diagram small" role="img" aria-label="{_e(name)} and its neighbours">']
    for side, items, x in (("in", left, 20), ("out", right, 560)):
        for i, n in enumerate(items):
            y = 15 + i * 28 + (h - 30 - len(items) * 28) / 2
            x1, x2 = (x + 180, 290) if side == "in" else (470, x)
            out.append(f'<path d="M{x1},{y + 11} C{(x1 + x2) / 2},{y + 11} {(x1 + x2) / 2},{cy} {x2},{cy}" '
                       f'class="wire thin"/>')
            out.append(f'<g class="box mini"><rect x="{x}" y="{y}" width="180" height="22" rx="8"/>'
                       f'<text x="{x + 90}" y="{y + 15}" class="s">{_e(n)}</text></g>')
    out.append(_box(290, cy - 25, 180, 50, name, "", "box main"))
    out.append(f'<text x="110" y="{h - 4}" class="s">used by</text><text x="650" y="{h - 4}" class="s">uses</text>')
    out.append("</svg>")
    return "".join(out)


CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1c1a;--dim:#6b6860;--line:#d9d5cc;--acc:#c96442;--pub:#3d8b5a;--per:#b8577a}
@media (prefers-color-scheme:dark){:root{--bg:#1b1a18;--card:#262421;--ink:#ece9e2;--dim:#a19d93;--line:#45413b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:24px;margin:8px 0}h2{font-size:19px;margin:28px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:10px 0}
input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);
color:var(--ink);font-size:15px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.45 ui-monospace,
monospace;margin:8px 0 0}details summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}main{overflow-x:hidden}.muted{color:var(--dim);font-size:13px}
.diagram{width:100%;height:auto;display:block}.diagram .box rect{fill:var(--card);stroke:var(--line);stroke-width:1.5}
.diagram .main rect{stroke:var(--acc);stroke-width:2.5}.diagram .t{font-size:15px;font-weight:600;fill:var(--ink);
text-anchor:middle}.diagram .s{font-size:12.5px;fill:var(--dim);text-anchor:middle}
.diagram .wire{fill:none;stroke:var(--line);stroke-width:2}.diagram .thin{stroke-width:1.2}
.diagram .dot{fill:var(--acc)}.diagram .dot.back{fill:var(--pub)}.diagram .mini rect{stroke-width:1}
.diagram .step rect{animation-name:lit;animation-iteration-count:infinite}
@keyframes lit{0%{fill:var(--acc);fill-opacity:.28}12%{fill:var(--card);fill-opacity:1}100%{fill:var(--card)}}
.group rect{fill:var(--card);stroke:var(--line)}.gt{font-weight:600;font-size:13.5px;fill:var(--ink)}
.svc{font-size:12px;fill:var(--ink)}.personal{fill:var(--per)}.public{fill:var(--pub)}
pre.doc{font:14px/1.5 system-ui,sans-serif}h3{font-size:15px;margin:14px 0 4px}ul{padding-left:20px;margin:4px 0}li{margin:3px 0}
.legend span{display:inline-block;width:10px;height:10px;border-radius:5px;margin:0 6px 0 12px}
@media (prefers-reduced-motion:reduce){.diagram .dot{display:none}.diagram .step rect{animation:none}}
"""

JS = """
const q=document.getElementById('q');q.addEventListener('input',()=>{const w=q.value.toLowerCase().trim();
document.querySelectorAll('[data-find]').forEach(el=>{const hit=!w||el.dataset.find.includes(w);
el.style.display=hit?'':'none';if(w&&hit&&el.tagName==='DETAILS')el.open=true;});});
"""


def _free_models_line() -> str:
    names = {"github": "GitHub Models", "openrouter": "OpenRouter", "deepseek": "DeepSeek (via OpenRouter)"}
    try:
        from . import free_models  # noqa: PLC0415
        return "; ".join(f"{names.get(n, n.title())}: {p.get('good_at', '')}" for n, p in free_models.PROVIDERS.items())
    except Exception:
        return ""


def _para(text: str) -> str:
    """A paragraph of a module's description: lists and tables written with indents keep their lines."""
    lines = text.splitlines()
    if len(lines) > 1 and any(ln.startswith(("  ", "-", "1.", "  -")) or re.match(r"^\s*\w[\w ]{0,18}\s{2,}\S", ln)
                              for ln in lines[1:]):
        return f"<pre class='doc'>{_e(text)}</pre>"
    return f"<p>{_e(' '.join(text.split()))}</p>"


def components_html(modules: dict) -> str:
    diagrams = {"Buddy Connect": steps("Buddy Connect", CONNECT_STEPS, 0.9),
                "Free AI models": steps("Free models", FREE_STEPS, 1.0)}
    out = []
    for c in codedocs.components(modules):
        key = c["name"].split(" (")[0]
        extra = (f"<p class='muted'>Providers: {_e(_free_models_line())}</p>" if key == "Free AI models" else "")
        paras = "".join(_para(p) for p in c["explanation"].split("\n\n") if p.strip())
        others = "".join(f"<li>{_e(o)}</li>" for o in c["others"])
        find = _e((c["name"] + " " + c["role"] + " " + c["explanation"] + " " + " ".join(c["others"])).lower())
        out.append(f"<details class='card' data-find='{find}'{' open' if key in diagrams else ''}><summary>"
                   f"{_e(c['name'])}<br><span class='muted'>{_e(c['role'])}</span></summary>"
                   f"{diagrams.get(key, '')}{extra}<h3>How it works</h3>{paras}"
                   + (f"<h3>Its parts</h3><ul>{others}</ul>" if others else "")
                   + f"<p class='muted'>Code: {_e(', '.join(c['modules']))}</p></details>")
    return "".join(out)


def html() -> str:
    _db, pages = codedocs._index()
    modules = {k: p for k, p in pages.items() if p["kind"] == "server"}
    stats = {"modules": len(modules), "tools": pages["tools"]["text"].count("\n- ") + 1,
             "endpoints": pages["endpoints"]["text"].count("\n- ") + 1,
             "tables": pages["tables"]["text"].count("\n- ") + 1,
             "services": pages["connect"]["text"].count("\n- ") + 1 if pages["connect"]["text"] else 0}
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
             f"content='width=device-width,initial-scale=1'><title>Buddy: how it works</title><style>{CSS}</style>"
             f"</head><body><main><h1>Buddy: how it works</h1><p class='muted'>Generated from the code running on "
             f"Buddy's server. Search filters everything below.</p><input id='q' placeholder='Search: calls, "
             f"memory, Plivo, convolog…' aria-label='Search'>"]
    parts.append(f"<h2>Architecture</h2><div class='card'>{architecture(stats)}<p class='muted'>Orange dots: "
                 f"requests going out. Green: answers coming back.</p></div>")
    raw = {}
    for r, p in codedocs.build_modules().items():
        raw[r] = p
    parts.append(f"<h2>Components</h2><p class='muted'>The big parts of Buddy, explained from the descriptions "
                 f"in their own code. Tap one to open it.</p>{components_html(raw)}")
    parts.append(f"<h2>A chat message, step by step</h2><div class='card'>{steps('Chat message', CHAT)}</div>")
    parts.append(f"<h2>A phone call, step by step</h2><div class='card'>{steps('Phone call', CALL)}</div>")
    parts.append("<h2>Buddy Connect</h2><div class='card'><p class='legend muted'><span class='per' "
                 "style='background:var(--per)'></span>may see personal data<span style='background:var(--pub)'>"
                 f"</span>public data only</p>{connect_map()}</div>")
    for key, heading in (("tools", "Tools"), ("endpoints", "Server endpoints"), ("tables", "Database tables"),
                         ("settings", "Settings")):
        parts.append(f"<details class='card' data-find='{_e(pages[key]['text'].lower())}'><summary>{heading}"
                     f"</summary><pre>{_e(pages[key]['text'])}</pre></details>")
    parts.append("<h2>Server modules</h2>")
    used_by: dict[str, list[str]] = {}
    for k, p in modules.items():
        for line in p["text"].splitlines():
            if line.startswith("Uses: "):
                for u in line[6:].split(", "):
                    used_by.setdefault(u, []).append(Path(k).stem)
    for k, p in sorted(modules.items()):
        if Path(k).name == "__init__.py" and p["text"].startswith("(no description"):
            continue
        stem = Path(k).stem
        uses = next((ln[6:].split(", ") for ln in p["text"].splitlines() if ln.startswith("Uses: ")), [])
        about = p["text"].split("\n\n", 1)[0]
        parts.append(f"<details class='card' data-find='{_e((k + ' ' + p['text']).lower())}'><summary>"
                     f"{_e(k.removeprefix('backend/buddy/'))} <span class='muted'>{_e(' '.join(about.split())[:110])}</span></summary>"
                     f"{neighbours(stem, uses, sorted(set(used_by.get(stem, []))))}<pre>{_e(p['text'])}</pre>"
                     f"</details>")
    parts.append("<h2>The app and the guides</h2>")
    for k, p in sorted(pages.items()):
        if p["kind"] in ("app", "deploy", "guide"):
            parts.append(f"<details class='card' data-find='{_e((k + ' ' + p['text']).lower())}'><summary>{_e(k)}"
                         f"</summary><pre>{_e(p['text'][:codedocs.MAX_PAGE])}</pre></details>")
    parts.append(f"</main><script>{JS}</script></body></html>")
    return "".join(parts)
