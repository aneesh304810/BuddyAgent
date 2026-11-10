"""Technical documentation generated from Buddy's own code, so Buddy can explain its design and details.

Built from the code that's running (the same checkout selfcode.py reads), with no model and no cost:
  - every Python module: what it's for (its docstring), its classes and functions with their signatures and
    descriptions, the tools it gives Buddy, the server endpoints, the database tables, the settings it reads, and
    which other modules it uses and is used by;
  - every Kotlin file of the app: its documented classes, screens and functions;
  - every deploy script: what its header says it does;
  - the hand-written guides in docs/: one section per heading;
  - index pages across the whole code: the design overview, all tools, endpoints, settings, tables and the Buddy
    Connect services.
The pages are rebuilt whenever the code changes, kept in memory only, and searched with SQLite's full-text index
(word forms included: "booking" finds "book"). Code holds no personal data (selfcode skips files that could).
"""

import ast
import re
import sqlite3
import threading
from pathlib import Path

from . import selfcode

MAX_PAGE = 7000
_lock = threading.Lock()
_cache: dict = {"sig": None, "db": None, "pages": {}}

SETTING = re.compile(r"\b(BUDDY_[A-Z0-9_]{3,})\b")
TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)", re.I)
ROUTE = ("get", "post", "put", "delete", "websocket", "patch")


# --- one Python module ------------------------------------------------------------------------------------------
def _first(text: str | None, n: int = 1) -> str:
    paras = [p.strip() for p in (text or "").strip().split("\n\n") if p.strip()]
    return " ".join(" ".join(p.split()) for p in paras[:n])


def _sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(fn.args)
    except Exception:
        args = "..."
    ret = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    return f"{'async ' if isinstance(fn, ast.AsyncFunctionDef) else ''}{fn.name}({args}){ret}"


def _tools(tree: ast.Module) -> list[dict]:
    """Tool definitions: dicts with a name, a description and an input_schema."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if not {"name", "description", "input_schema"} <= set(keys):
            continue
        vals = dict(zip(keys, node.values))
        try:
            name = ast.literal_eval(vals["name"])
            desc = ast.literal_eval(vals["description"])
        except Exception:
            continue
        actions = []
        for sub in ast.walk(vals["input_schema"]):
            if isinstance(sub, ast.Dict):
                inner = {k.value: v for k, v in zip(sub.keys, sub.values) if isinstance(k, ast.Constant)}
                if "enum" in inner and isinstance(inner["enum"], ast.List):
                    try:
                        actions = ast.literal_eval(inner["enum"])
                        break
                    except Exception:
                        pass
        out.append({"name": str(name), "description": " ".join(str(desc).split()), "actions": actions})
    return out


def _routes(tree: ast.Module) -> list[dict]:
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in ROUTE
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                admin = "require_admin" in ast.unparse(dec)
                out.append({"method": dec.func.attr.upper(), "path": dec.args[0].value, "function": node.name,
                            "admin": admin, "about": _first(ast.get_docstring(node))})
    return out


def python_page(rel: str, text: str) -> dict:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"title": rel, "about": "", "body": "(couldn't be parsed)", "tools": [], "routes": [], "tables": [],
                "settings": [], "uses": []}
    about = ast.get_docstring(tree) or ""
    lines = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            lines.append(f"class {node.name}: {_first(ast.get_docstring(node))}".rstrip(": "))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("__"):
                    doc = _first(ast.get_docstring(sub))
                    lines.append(f"  .{_sig(sub)}" + (f": {doc}" if doc else ""))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = _first(ast.get_docstring(node))
            if not node.name.startswith("_") or doc:
                lines.append(f"{_sig(node)}" + (f": {doc}" if doc else ""))
    uses = sorted({(n.module or "").split(".")[0] for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom) and n.level >= 1 and n.module}
                  | {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level >= 1
                     and not n.module for a in n.names})
    return {"title": rel, "about": about, "body": "\n".join(lines), "tools": _tools(tree), "routes": _routes(tree),
            "tables": sorted(set(TABLE.findall(text))), "settings": sorted(set(SETTING.findall(text))),
            "uses": [u for u in uses if u]}


# --- Kotlin, shell, guides ---------------------------------------------------------------------------------------
KDOC = re.compile(r"/\*\*(.*?)\*/\s*(?:@\w+(?:\([^)]*\))?\s*)*((?:private |internal |data |sealed |enum |abstract |open )*"
                  r"(?:class|object|interface|fun)\s+[\w.<>, ]+)", re.S)


def kotlin_page(rel: str, text: str) -> dict:
    items = []
    for doc, decl in KDOC.findall(text):
        doc = " ".join(line.strip().lstrip("*").strip() for line in doc.splitlines()).strip()
        items.append(f"{' '.join(decl.split())}: {doc}")
    screens = sorted(set(re.findall(r"@Composable\s+(?:private\s+)?fun\s+(\w+)", text)))
    about = items[0].split(": ", 1)[-1] if items else ""
    body = "\n".join(items) + (f"\nScreens and parts (Compose): {', '.join(screens)}" if screens else "")
    return {"title": rel, "about": about, "body": body, "settings": sorted(set(SETTING.findall(text)))}


def shell_page(rel: str, text: str) -> dict:
    head = []
    for line in text.splitlines()[1:40]:
        if line.startswith("#"):
            head.append(line.lstrip("#").strip())
        elif head and line.strip():
            break
    return {"title": rel, "about": " ".join(head), "body": "\n".join(head),
            "settings": sorted(set(SETTING.findall(text)))}


def guide_sections(rel: str, text: str) -> list[dict]:
    out, title, buf = [], rel, []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                out.append({"title": f"{rel}: {title}", "body": "\n".join(buf).strip()})
            title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf:
        out.append({"title": f"{rel}: {title}" if title != rel else rel, "body": "\n".join(buf).strip()})
    return [s for s in out if s["body"]]


# --- the whole code ----------------------------------------------------------------------------------------------
def _signature() -> tuple:
    newest, count = 0.0, 0
    for _rel, path in selfcode._files():
        count += 1
        newest = max(newest, path.stat().st_mtime)
    return newest, count


def build() -> dict[str, dict]:
    """Every page: {key: {"title", "kind", "text"}}."""
    pages: dict[str, dict] = {}
    modules: dict[str, dict] = {}
    for rel, path in selfcode._files():
        r = str(rel)
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if r.endswith(".py") and not r.startswith("backend/tests/"):
            modules[r] = python_page(r, text)
        elif r.endswith(".kt"):
            p = kotlin_page(r, text)
            if p["body"]:
                pages[r] = {"title": r, "kind": "app", "text": _kotlin_text(p)}
        elif r.endswith(".sh"):
            p = shell_page(r, text)
            if p["body"]:
                pages[r] = {"title": r, "kind": "deploy", "text": p["body"] + _settings_line(p["settings"])}
        elif r.endswith(".md") and (r.startswith("docs/") or r == "README.md"):
            for s in guide_sections(r, text):
                pages[s["title"]] = {"title": s["title"], "kind": "guide", "text": s["body"]}
    used_by: dict[str, list[str]] = {}
    for r, m in modules.items():
        for u in m["uses"]:
            used_by.setdefault(u, []).append(Path(r).stem)
    for r, m in modules.items():
        pages[r] = {"title": r, "kind": "server", "text": _module_text(m, used_by.get(Path(r).stem, []))}
    pages.update(_index_pages(modules, pages))
    return pages


def _settings_line(settings: list[str]) -> str:
    return f"\nSettings it reads: {', '.join(settings)}" if settings else ""


def _kotlin_text(p: dict) -> str:
    return (p["body"] + _settings_line(p["settings"]))[:MAX_PAGE]


def _module_text(m: dict, used_by: list[str]) -> str:
    parts = [m["about"] or "(no description at the top of this file)"]
    if m["tools"]:
        parts.append("Tools it gives Buddy:\n" + "\n".join(
            f"- {t['name']}" + (f" ({', '.join(map(str, t['actions']))})" if t["actions"] else "") + f": {t['description']}"
            for t in m["tools"]))
    if m["routes"]:
        parts.append("Server endpoints:\n" + "\n".join(
            f"- {x['method']} {x['path']}" + (" (admin)" if x["admin"] else "") + (f": {x['about']}" if x["about"] else "")
            for x in m["routes"]))
    if m["tables"]:
        parts.append("Database tables: " + ", ".join(m["tables"]))
    if m["settings"]:
        parts.append("Settings it reads: " + ", ".join(m["settings"]))
    if m["uses"]:
        parts.append("Uses: " + ", ".join(m["uses"]))
    if used_by:
        parts.append("Used by: " + ", ".join(sorted(set(used_by))))
    if m["body"]:
        parts.append("Classes and functions:\n" + m["body"])
    return "\n\n".join(parts)[:MAX_PAGE * 2]


def _index_pages(modules: dict[str, dict], pages: dict[str, dict]) -> dict[str, dict]:
    out = {}
    server = sorted((r, m) for r, m in modules.items() if r.startswith("backend/buddy/"))
    out["design"] = {"title": "Design overview", "kind": "index", "text": (
        "Buddy is a personal assistant: an Android app (android/, Kotlin) talks over a WebSocket to a server "
        "(backend/buddy, Python, FastAPI) on the owner's own server, reached only through Tailscale. Claude is the "
        "main brain; free models do general work without personal data; outside services go through Buddy Connect "
        "(gateway.py). deploy/ holds the server's setup and update scripts.\n\nServer modules:\n" + "\n".join(
            f"- {Path(r).name}: {_first(m['about'])[:220]}" for r, m in server)
        + "\n\nApp files:\n" + "\n".join(f"- {Path(k).name}: {v['text'].split(chr(10))[0][:160]}"
                                         for k, v in sorted(pages.items()) if v["kind"] == "app")
        + "\n\nGuides: " + ", ".join(sorted({k.split(':')[0] for k, v in pages.items() if v['kind'] == 'guide'})))}
    tools = [(Path(r).name, t) for r, m in server for t in m["tools"]]
    out["tools"] = {"title": "All of Buddy's tools", "kind": "index", "text": "\n".join(
        f"- {t['name']} ({f}): {t['description'][:300]}" for f, t in sorted(tools, key=lambda x: x[1]["name"]))}
    routes = [(Path(r).name, x) for r, m in server for x in m["routes"]]
    out["endpoints"] = {"title": "Server endpoints", "kind": "index", "text": "\n".join(
        f"- {x['method']} {x['path']}" + (" (admin)" if x["admin"] else "") + f" ({f})"
        + (f": {x['about']}" if x["about"] else "") for f, x in routes)}
    settings: dict[str, set] = {}
    for r, m in modules.items():
        for s in m["settings"]:
            settings.setdefault(s, set()).add(Path(r).name)
    out["settings"] = {"title": "Settings (server configuration)", "kind": "index", "text": "\n".join(
        f"- {s}: {', '.join(sorted(f))}" for s, f in sorted(settings.items()))}
    tables = [(t, Path(r).name) for r, m in modules.items() for t in m["tables"]]
    out["tables"] = {"title": "Database tables", "kind": "index", "text": "\n".join(
        f"- {t} ({f})" for t, f in sorted(set(tables)))}
    out["connect"] = {"title": "Buddy Connect services", "kind": "index", "text": _connect_text()}
    return out


def _connect_text() -> str:
    try:
        from . import gateway  # noqa: PLC0415
    except Exception:
        return ""
    lines = []
    for group in gateway.GROUPS + ["Other"]:
        names = [n for n in gateway.CATALOG if gateway.group_of(n) == group]
        for n in names:
            e = gateway.CATALOG[n]
            lines.append(f"- {group} / {e.get('title', n)} [{n}]: {e.get('used_for', '')}; data: "
                         f"{e.get('privacy', 'public')}; auth: {(e.get('auth') or {}).get('type', 'none')}")
    return "\n".join(lines)


# --- search ------------------------------------------------------------------------------------------------------
def _index() -> tuple[sqlite3.Connection, dict]:
    with _lock:
        sig = _signature()
        if _cache["sig"] != sig or _cache["db"] is None:
            pages = build()
            db = sqlite3.connect(":memory:", check_same_thread=False)
            db.execute("CREATE VIRTUAL TABLE docs USING fts5(key UNINDEXED, title, text, tokenize='porter')")
            db.executemany("INSERT INTO docs (key, title, text) VALUES (?, ?, ?)",
                           [(k, p["title"], p["text"]) for k, p in pages.items()])
            _cache.update(sig=sig, db=db, pages=pages)
        return _cache["db"], _cache["pages"]


def _fts_query(query: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9_]{2,}", query.lower())
             if w not in {"the", "and", "how", "does", "what", "why", "is", "are", "do", "of", "to", "in", "a", "an",
                          "it", "buddy", "work", "works", "explain", "about", "me", "you", "your", "for", "with"}]
    return " OR ".join(f'"{w}"' for w in words[:12])


def search(query: str, limit: int = 6) -> str:
    db, pages = _index()
    q = _fts_query(query or "")
    if not q:
        return pages["design"]["text"][:MAX_PAGE]
    with _lock:
        rows = db.execute("SELECT key, title, snippet(docs, 2, '', '', ' … ', 40) FROM docs WHERE docs MATCH ? "
                          "ORDER BY bm25(docs, 0, 5.0, 1.0) LIMIT ?", (q, max(1, min(int(limit or 6), 12)))).fetchall()
    if not rows:
        return f"No documentation matches '{query}'. Try other words, or search the code itself."
    return "\n\n".join(f"[{key}] {title}\n{' '.join(snip.split())}" for key, title, snip in rows) + \
        "\n\n(Open one with doc and its [key].)"


def page(key: str) -> str:
    _db, pages = _index()
    key = (key or "design").strip()
    hit = pages.get(key)
    if hit is None:
        matches = [k for k in pages if k.endswith("/" + key) or k.endswith(key) or Path(k).stem == key]
        order = {"server": 0, "index": 1, "app": 2, "deploy": 3, "guide": 4}  # "calls": the module before the script
        hit = pages.get(min(matches, key=lambda k: (order.get(pages[k]["kind"], 9), len(k)))) if matches else None
    if hit is None:
        raise selfcode.ToolError(f"No documentation page '{key}'. Use docs to search, or design for the overview.")
    text = hit["text"]
    more = f"\n(… {len(text) - MAX_PAGE} more characters: read the file itself for the rest)" if len(text) > MAX_PAGE \
        else ""
    return f"{hit['title']} ({hit['kind']})\n\n{text[:MAX_PAGE]}{more}"


def export() -> str:
    """All the documentation as one Markdown document (python -m buddy.codedocs > TECHNICAL.md)."""
    _db, pages = _index()
    order = ["design", "tools", "endpoints", "connect", "tables", "settings"]
    rest = sorted(k for k in pages if k not in order)
    return "\n\n".join(f"## {pages[k]['title']}\n\n{pages[k]['text']}" for k in order + rest if k in pages)


if __name__ == "__main__":
    print("# Buddy: technical documentation (generated from the code)\n\n" + export())
