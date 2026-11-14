"""Feature documentation with diagrams, generated from the code: for every feature of Buddy, its technical design
and an architecture diagram, so Buddy can explain any part of itself and show how the pieces connect.

  A feature is one entry of codedocs.COMPONENTS: a name, what it does, and the server modules that make it up.
  For each one this builds, from the running code and with no model:
    design      each module's own description (its docstring is the design note, kept next to the code it
                describes, so it can't drift from it)
    tools       what Buddy can do in this feature (the tool names and what each is for)
    data        the database tables the feature keeps, the settings it reads, the server endpoints it serves
    services    the outside services it calls through Buddy Connect, with whether each may see personal data
    links       the other features it relies on, and the ones that rely on it
    diagram     you -> the tools -> the modules -> the database, the outside services and the other features

  Three ways it is used:
    in conversation  "how does the deals collector work?", "show me the investing architecture": Buddy answers
                     from the design and the diagram appears in the chat as an image (Graphviz on the server)
    the whole thing  "show me Buddy's architecture": every feature in one diagram
    as documents     python -m buddy.codedocs_features docs/architecture writes one Markdown page per feature
                     (diagrams as Mermaid, which GitHub draws) and an overview page

Code holds no personal data, and nothing here leaves the server.
"""

import json
import re
import sys
from pathlib import Path

from . import codedocs, gateway

HERE = Path(__file__).parent
SERVICE = re.compile(r"""gateway\.call(?:_url)?\(\s*[\w.]+\s*,\s*["'](\w+)["']""")
ARCH_WORDS = re.compile(r"\b(whole|overall|entire|system|everything|all (?:the )?features|big picture|"
                        r"architecture of buddy|buddy'?s architecture)\b", re.I)
MAX_TEXT = 6500


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().split(":")[0].split("(")[0]).strip("-")


def _short(text: str, n: int = 70) -> str:
    first = codedocs._first(text).split(". ")[0].split(": ", 1)[-1] if ": " in codedocs._first(text)[:40] \
        else codedocs._first(text).split(". ")[0]
    first = first.strip().rstrip(".")
    return first if len(first) <= n else first[:n].rsplit(" ", 1)[0] + "…"


def build() -> list[dict]:
    """Every feature with what the code says about it."""
    modules = {Path(r).name: m for r, m in codedocs.build_modules().items()
               if r.startswith("backend/buddy/") and "/" not in r.removeprefix("backend/buddy/")}
    owner = {f: name for name, _, files in codedocs.COMPONENTS for f in files}
    out = []
    for name, role, files in codedocs.COMPONENTS:
        have = [f for f in files if f in modules]
        mods = []
        for f in have:
            m = modules[f]
            try:
                source = (HERE / f).read_text()
            except OSError:
                source = ""
            services = sorted({s for s in SERVICE.findall(source) if s in gateway.CATALOG})
            mods.append({"file": f, "about": m["about"], "short": _short(m["about"]),
                         "tools": [{"name": t["name"], "what": codedocs._first(t["description"]).split(". ")[0][:160],
                                    "actions": t["actions"]} for t in m["tools"]],
                         "tables": m["tables"], "settings": m["settings"], "routes": m["routes"],
                         "services": services,
                         "uses": [u + ".py" for u in m["uses"] if u + ".py" in modules and u + ".py" != f]})
        inside = set(have)
        uses = sorted({owner[u] for m in mods for u in m["uses"] if u not in inside and owner.get(u) != name})
        out.append({"name": name, "slug": _slug(name), "role": role, "modules": mods, "uses_features": uses})
    for f in out:
        f["used_by_features"] = sorted(o["name"] for o in out if f["name"] in o["uses_features"])
    return out


def find(query: str, features: list[dict] | None = None) -> dict | None:
    """The feature a question is about: by its name, its modules, its tools, then the words of its description."""
    features = features or build()
    words = [w for w in re.findall(r"[a-z0-9_]{3,}", (query or "").lower())
             if w not in ("how", "does", "the", "work", "works", "show", "diagram", "architecture", "design", "buddy",
                          "about", "tell", "explain", "what", "with", "and", "for", "feature", "technical", "your")]
    if not words:
        return None
    best, top = None, 0.0
    for f in features:
        score = 0.0
        name, role = f["name"].lower(), f["role"].lower()
        files = " ".join(m["file"] for m in f["modules"]).lower()
        tools = " ".join(t["name"] for m in f["modules"] for t in m["tools"]).lower()
        about = " ".join(m["about"] for m in f["modules"]).lower()
        for w in words:
            stem = w.rstrip("s")
            if len(stem) >= 4:  # a short word ("day", "the") isn't enough to name a feature or a module
                hit = lambda text: any(tok.startswith(stem) or (len(tok) >= 4 and stem.startswith(tok))  # noqa: E731
                                       for tok in re.findall(r"[a-z0-9]+", text))
                score += 5 * hit(name) + 4 * hit(files) + 4 * hit(tools) + 2 * hit(role)
            score += min(about.count(stem), 5) * 0.3
        if score > top:
            best, top = f, score
    return best if top >= 2 else None


# --- diagrams ---------------------------------------------------------------------------------------------------
def _wrap(text: str, width: int = 24) -> str:
    lines, line = [], ""
    for w in str(text).split():
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return "\\n".join(lines + [line] if line else lines)


def _q(text: str) -> str:
    return str(text).replace("\\", "/").replace('"', "'")


def dot(f: dict) -> str:
    """Graphviz text for one feature: you -> tools -> modules -> database, services, other features."""
    lines = ["digraph feature {", "rankdir=LR;", f'label="{_q(f["name"])}"; labelloc=t; fontsize=16;',
             'you [label="You\\n(voice, chat, the app)" fillcolor="#FFF4D6" color="#C8862A"];']
    tools = [(t["name"], m["file"]) for m in f["modules"] for t in m["tools"]]
    if tools:
        lines.append('subgraph cluster_tools { label="What Buddy can do (tools)"; color="#C8862A"; style=dashed;')
        lines += [f'"t_{n}" [label="{_q(n)}" fillcolor="#FFF8EE" color="#C8862A"];' for n, _ in tools[:10]]
        lines.append("}")
    lines.append('subgraph cluster_mods { label="Modules"; color="#1F6F5C";')
    lines += [f'"{m["file"]}" [label="{_q(m["file"])}\\n{_q(_wrap(m["short"]))}" fillcolor="#F2F8F6" color="#1F6F5C"];'
              for m in f["modules"]]
    lines.append("}")
    for n, mod in tools[:10]:
        lines += [f'you -> "t_{n}";', f'"t_{n}" -> "{mod}";']
    if not tools and f["modules"]:
        lines.append(f'you -> "{f["modules"][0]["file"]}";')
    inside = {m["file"] for m in f["modules"]}
    for m in f["modules"]:
        lines += [f'"{m["file"]}" -> "{u}";' for u in m["uses"] if u in inside]
    tables = sorted({t for m in f["modules"] for t in m["tables"]})
    if tables:
        shown = ", ".join(tables[:8]) + (f" (+{len(tables) - 8} more)" if len(tables) > 8 else "")
        lines.append(f'db [label="Your database\\n{_q(_wrap(shown, 30))}" shape=cylinder fillcolor="#EEF3FF"];')
        lines += [f'"{m["file"]}" -> db;' for m in f["modules"] if m["tables"]]
    services = sorted({s for m in f["modules"] for s in m["services"]})
    for s in services[:8]:
        entry = gateway.CATALOG[s]
        personal = entry.get("privacy") == "personal"
        lines.append(f'"s_{s}" [label="{_q(_wrap(entry["title"], 22))}\\n({"may see personal data" if personal else "public data only"})" '
                     f'fillcolor="{"#FBEBEB" if personal else "#FFFFFF"}" color="{"#B3261E" if personal else "#6B7280"}"];')
        lines += [f'"{m["file"]}" -> "s_{s}";' for m in f["modules"] if s in m["services"]]
    for i, other in enumerate(f["uses_features"][:6]):
        lines.append(f'"o_{i}" [label="{_q(_wrap(other.split(":")[0].split("(")[0], 22))}" style="rounded,dashed" '
                     'fillcolor="#FFFFFF"];')
        lines.append(f'"{f["modules"][0]["file"]}" -> "o_{i}" [style=dashed label="uses"];')
    return "\n".join(lines + ["}"])


def system_dot(features: list[dict] | None = None) -> str:
    """Graphviz text for the whole of Buddy: the phone, the server's features, the stores and the outside world."""
    features = features or build()
    lines = ["digraph buddy {", "rankdir=LR;", 'label="Buddy: the whole system"; labelloc=t; fontsize=18;',
             'phone [label="Your phone\\nthe Buddy app: voice, chat, pages" fillcolor="#FFF4D6" color="#C8862A"];',
             'net [label="Tailscale\\nprivate network" shape=ellipse fillcolor="#E5F2EE" color="#1F6F5C"];',
             'subgraph cluster_server { label="Your server"; color="#1F6F5C";']
    lines += [f'"f_{f["slug"]}" [label="{_q(_wrap(f["name"].split(":")[0].split("(")[0], 24))}\\n{len(f["modules"])} modules" '
              'fillcolor="#F2F8F6" color="#1F6F5C"];' for f in features]
    lines += ["}", 'db [label="Encrypted database\\n(your data)" shape=cylinder fillcolor="#EEF3FF"];',
              'book [label="Price book\\n(market data)" shape=cylinder fillcolor="#EEF3FF"];',
              'box [label="Sandbox\\n(code, learning, browser)" fillcolor="#FFFFFF"];',
              'claude [label="Claude\\n(the only AI that sees\\npersonal data)" fillcolor="#FBEBEB" color="#B3261E"];',
              'free [label="Free AI models\\n(public text only)" fillcolor="#FFFFFF"];',
              'world [label="Outside services\\nthrough Buddy Connect" fillcolor="#FFFFFF"];',
              "phone -> net;", 'net -> "f_the-server-and-the-app";', '"f_the-server-and-the-app" -> "f_claude";',
              '"f_claude" -> claude;', '"f_free-ai-models" -> free;', '"f_buddy-connect" -> world;',
              '"f_memory" -> db;', '"f_the-price-book-pattern-learning-and-trained-models" -> book;',
              '"f_the-price-book-pattern-learning-and-trained-models" -> box;', '"f_making-and-doing-things" -> box;']
    slugs = {f["slug"] for f in features}
    core = {"claude", "the-server-and-the-app", "memory", "buddy-connect", "free-ai-models"}
    for f in features:
        if f["slug"] not in core:
            lines.append(f'"f_claude" -> "f_{f["slug"]}" [color="#9AA1AD"];')
    return "\n".join(ln for ln in lines if not re.search(r'"f_([a-z0-9-]+)"', ln)
                     or all(s in slugs for s in re.findall(r'"f_([a-z0-9-]+)"', ln))) + "\n}"


def mermaid(f: dict) -> str:
    """The same picture as Mermaid text, for the Markdown pages."""
    def nid(s: str) -> str:
        return re.sub(r"\W", "_", s)

    def lab(s: str) -> str:
        return str(s).replace('"', "'").replace("\\n", "<br/>")

    out = ["flowchart LR", '    you(["You: voice, chat, the app"])']
    tools = [(t["name"], m["file"]) for m in f["modules"] for t in m["tools"]][:10]
    if tools:
        out.append('    subgraph Tools["What Buddy can do"]')
        out += [f'        t_{nid(n)}["{lab(n)}"]' for n, _ in tools]
        out.append("    end")
    out.append('    subgraph Modules["Modules"]')
    out += [f'        {nid(m["file"])}["{lab(m["file"])}<br/>{lab(m["short"])}"]' for m in f["modules"]]
    out.append("    end")
    for n, mod in tools:
        out += [f"    you --> t_{nid(n)}", f"    t_{nid(n)} --> {nid(mod)}"]
    if not tools and f["modules"]:
        out.append(f'    you --> {nid(f["modules"][0]["file"])}')
    inside = {m["file"] for m in f["modules"]}
    for m in f["modules"]:
        out += [f'    {nid(m["file"])} --> {nid(u)}' for u in m["uses"] if u in inside]
    tables = sorted({t for m in f["modules"] for t in m["tables"]})
    if tables:
        out.append(f'    db[("Your database<br/>{lab(", ".join(tables[:8]))}{" and more" if len(tables) > 8 else ""}")]')
        out += [f'    {nid(m["file"])} --> db' for m in f["modules"] if m["tables"]]
    for s in sorted({s for m in f["modules"] for s in m["services"]})[:8]:
        entry = gateway.CATALOG[s]
        note = "may see personal data" if entry.get("privacy") == "personal" else "public data only"
        out.append(f'    s_{nid(s)}["{lab(entry["title"])}<br/>({note})"]')
        out += [f'    {nid(m["file"])} --> s_{nid(s)}' for m in f["modules"] if s in m["services"]]
    for i, other in enumerate(f["uses_features"][:6]):
        out.append(f'    o_{i}["{lab(other.split(":")[0].split("(")[0].strip())}"]')
        out.append(f'    {nid(f["modules"][0]["file"])} -. uses .-> o_{i}')
    return "\n".join(out)


# --- text ---------------------------------------------------------------------------------------------------------
def design_text(f: dict, limit: int = MAX_TEXT) -> str:
    """The feature's technical design in plain text, for Buddy to answer from: the facts first, then each
    module's own description (shortened evenly when there are many, so none is dropped)."""
    head = [f"{f['name']}: {f['role']}.", ""]
    tools = [f"{t['name']}: {t['what']}" for m in f["modules"] for t in m["tools"]]
    if tools:
        head.append("Tools: " + "; ".join(tools)[:1400])
    tables = sorted({t for m in f["modules"] for t in m["tables"]})
    if tables:
        head.append("Database tables: " + ", ".join(tables))
    services = sorted({gateway.CATALOG[s]["title"] for m in f["modules"] for s in m["services"]})
    if services:
        head.append("Outside services (through Buddy Connect): " + ", ".join(services))
    if f["uses_features"]:
        head.append("Relies on: " + "; ".join(f["uses_features"]))
    if f["used_by_features"]:
        head.append("Relied on by: " + "; ".join(f["used_by_features"]))
    head += ["", "How each module works (from the code's own descriptions):"]
    budget = max(200, (limit - len("\n".join(head))) // max(1, len(f["modules"])) - 40)
    parts = []
    for m in f["modules"]:
        about = " ".join(m["about"].split()) if len(m["about"]) > budget else m["about"].strip()
        parts.append(f"[{m['file']}] " + (about if len(about) <= budget else about[:budget].rsplit(" ", 1)[0]
                                          + " … (ask about this module for the rest)"))
    return "\n".join(head + parts)[:limit]


def flow_steps(f: dict) -> str:
    """The diagram in words, for when it can't be drawn."""
    tools = [t["name"] for m in f["modules"] for t in m["tools"]]
    tables = sorted({t for m in f["modules"] for t in m["tables"]})
    services = sorted({gateway.CATALOG[s]["title"] for m in f["modules"] for s in m["services"]})
    return " -> ".join(x for x in (
        "You", f"tools ({', '.join(tools[:6])})" if tools else "", f"modules ({', '.join(m['file'] for m in f['modules'])})",
        f"your database ({', '.join(tables[:6])})" if tables else "",
        f"outside services ({', '.join(services[:6])})" if services else "") if x)


def markdown(f: dict) -> str:
    """One feature as a Markdown page: what it does, the diagram, the design module by module, and its data."""
    out = [f"# {f['name']}", "", f"{f['role']}.", "", "## Architecture", "", "```mermaid", mermaid(f), "```", "",
           "The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and "
           "what they keep or call. Dashed arrows are other features this one relies on.", ""]
    tools = [(t, m["file"]) for m in f["modules"] for t in m["tools"]]
    if tools:
        out += ["## What Buddy can do here", "", "| Tool | What it is for | Actions | Module |", "|---|---|---|---|"]
        out += [f"| `{t['name']}` | {t['what'].replace('|', '/')} | {', '.join(map(str, t['actions'])) or '-'} | {mod} |"
                for t, mod in tools]
        out.append("")
    out += ["## Technical design, module by module", ""]
    for m in f["modules"]:
        out += [f"### {m['file']}", "", "```text", m["about"].strip() or "(no description in the code yet)", "```", ""]
        facts = []
        if m["tables"]:
            facts.append("Tables: " + ", ".join(f"`{t}`" for t in m["tables"]))
        if m["services"]:
            facts.append("Outside services: " + ", ".join(gateway.CATALOG[s]["title"] for s in m["services"]))
        if m["settings"]:
            facts.append("Settings: " + ", ".join(f"`{s}`" for s in m["settings"][:12])
                         + (f" and {len(m['settings']) - 12} more" if len(m["settings"]) > 12 else ""))
        if m["routes"]:
            facts.append("Endpoints: " + ", ".join(f"`{r['method']} {r['path']}`" for r in m["routes"][:10])
                         + (f" and {len(m['routes']) - 10} more" if len(m["routes"]) > 10 else ""))
        if m["uses"]:
            facts.append("Uses: " + ", ".join(m["uses"]))
        out += [f"- {x}" for x in facts] + ([""] if facts else [])
    if f["uses_features"] or f["used_by_features"]:
        out += ["## How it connects", ""]
        if f["uses_features"]:
            out.append("- Relies on: " + "; ".join(f["uses_features"]))
        if f["used_by_features"]:
            out.append("- Relied on by: " + "; ".join(f["used_by_features"]))
        out.append("")
    return "\n".join(out)


OVERVIEW = """# Buddy: architecture and features

Generated from the code by `python -m buddy.codedocs_features docs/architecture`. Each feature page takes its
technical design from the descriptions kept in the modules themselves, so the pages match the code they were
generated from. Regenerate after changing the code.

## The whole system

```mermaid
flowchart LR
    phone(["Your phone: the Buddy app<br/>voice, chat, pages"])
    net{{"Tailscale<br/>private network"}}
    subgraph server["Your server"]
        srv["The server<br/>chat connection, app pages"]
        brain["Claude: the brain<br/>understands, decides, uses tools"]
        feats["The features<br/>(one page each, below)"]
        loop["Speaking up on its own<br/>briefing, reminders, alerts, lessons"]
        connect["Buddy Connect<br/>one door to every outside service"]
    end
    db[("Encrypted database<br/>your data")]
    book[("Price book<br/>market data only")]
    box["Sandbox<br/>code, learning, browser"]
    claude["Claude API<br/>the only AI that sees personal data"]
    free["Free AI models<br/>public text only"]
    world["Outside services<br/>banks, mail, travel, markets..."]
    phone <--> net <--> srv
    srv --> brain --> feats
    loop --> feats
    brain <--> claude
    feats --> connect --> world
    feats --> free
    feats <--> db
    feats <--> book
    feats --> box
```

## A question, end to end

```mermaid
sequenceDiagram
    participant You
    participant App as Buddy app
    participant S as Server
    participant M as Memory
    participant C as Claude
    participant T as A tool (a feature)
    You->>App: speak or type
    App->>S: the words (over Tailscale)
    S->>M: what do I already know about this?
    M-->>S: facts, passages, preferences
    S->>C: the question with that context
    C-->>S: use this tool, with these inputs
    S->>T: run it (asking your yes first if it acts)
    T-->>S: the result
    S->>C: the result
    C-->>S: the answer
    S-->>App: spoken sentences and cards
    App-->>You: voice, and cards on screen
```

## What learns from what

```mermaid
flowchart LR
    said["What you say"] --> tutor["English lessons"]
    said --> brief["What the briefing follows"]
    thumbs["Your thumbs"] --> loud["How loud each kind of alert is"]
    thumbs --> news["Which news you see"]
    prices["Prices and deals seen"] --> patterns["Price patterns (nightly, sandbox)"]
    calls["Buy-or-wait and book-or-wait calls"] --> outcomes["Judged against later prices"]
    signals["Buy and sell signals"] --> paper["Paper trades (practice book)"]
    outcomes --> weights["What each reason has earned"]
    paper --> weights
    weights --> next["The next call or signal"]
    outcomes --> model["A trained model, once 200 are judged<br/>used only if it passes its test"]
    paper --> model
    model --> next
```

## Rules the design keeps

- Buddy never pays, books, transfers, submits, cancels, buys or trades by itself: every action needs your yes.
- Your personal data goes only to Claude, never to a free model; outside services get the least they need.
- Keys are entered at a hidden prompt on the server, never in chat, and are kept out of logs and prompts.
- Your data lives in an encrypted database on your server; market data is kept apart in the price book.
- Every prediction is written down when made and judged later, and says how much it has been right.

## The features

| Feature | What it does | Modules |
|---|---|---|
"""


def write_docs(folder: str) -> list[str]:
    """One Markdown page per feature and the overview, into `folder`. Returns the files written."""
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    features = build()
    rows, written = [], []
    for i, f in enumerate(features, 1):
        name = f"{i:02d}-{f['slug']}.md"
        (root / name).write_text(markdown(f) + "\n")
        written.append(name)
        rows.append(f"| [{f['name']}]({name}) | {f['role']} | {len(f['modules'])} |")
    total = sum(len(f["modules"]) for f in features)
    (root / "README.md").write_text(OVERVIEW + "\n".join(rows) + f"\n\n{len(features)} features, {total} server "
                                    "modules. The app (Kotlin) and the deploy scripts are documented by "
                                    "`python -m buddy.codedocs`.\n")
    return ["README.md", *written]


# --- in conversation --------------------------------------------------------------------------------------------
def show(memory, query: str) -> str:
    """For Buddy's self-knowledge tool: the design of the feature asked about, with its diagram in the chat."""
    from . import makers  # noqa: PLC0415
    from .tools import ToolError  # noqa: PLC0415
    features = build()
    whole = not (query or "").strip() or bool(ARCH_WORDS.search(query or ""))
    f = None if whole else find(query, features)
    if not whole and f is None:
        return json.dumps({"note": "No feature matches that. The features are: " + "; ".join(x["name"] for x in features)
                                   + ". Ask about one of them, or search the docs."})
    if whole:
        title, graph = "Buddy: the whole system", system_dot(features)
        out = {"architecture": "Buddy: the whole system",
               "features": [{"name": x["name"], "does": x["role"], "modules": len(x["modules"])} for x in features],
               "flow": "your phone -> Tailscale -> the server -> Claude decides -> a feature's tool runs -> the "
                       "encrypted database, the price book, the sandbox, or an outside service through Buddy Connect"}
    else:
        title, graph = f"{f['name']}: architecture", dot(f)
        out = {"feature": f["name"], "design": design_text(f), "flow": flow_steps(f)}
    try:
        if memory is None:
            raise ToolError("no chat to show it in")
        file = makers.save(memory, makers.render_diagram(graph), title, "png", "diagram")
        out["diagram"] = "It's in the chat already (an image)."
        out["card"] = makers._show(makers._card(file, title))
    except ToolError as e:
        out["diagram"] = f"I couldn't draw it ({e}); the flow above says the same in words."
    out["how_to_answer"] = ("Explain it in plain words from the design above: what it does, how the parts work "
                            "together step by step, what it keeps and what it calls. Say the diagram is on screen.")
    return json.dumps(out)


if __name__ == "__main__":
    print("\n".join(write_docs(sys.argv[1] if len(sys.argv) > 1 else "docs/architecture")))
