"""Research desk: finds strategy research Buddy is allowed to fetch, queues it, and reads what you pick.

Only two kinds of source are fetched by program, because they are the ones that offer it:

  arXiv        the quantitative finance sections, through arXiv's own API (export.arxiv.org). Once a week Buddy
               lists new papers whose title or abstract looks like a testable trading strategy.
  GitHub       a public file or folder you point to (for example an open-source strategy library). Buddy reads
               the repository's licence first and will not take a file from a repository that states none.

SSRN, journal sites, strategy catalogues, blogs and books are not fetched: their terms allow a person to read
or download, not a program. Download those yourself and upload them in the Trading tab.

  queue    every find is a candidate with its title, link, date and source. Nothing is read by a model yet.
  read     when you pick one: the file is fetched, its text is taken out in the sandbox (docread.py), and the
           strategy is written as a proposed rule (stratrules.py) carrying its source link and licence.
  approve  as for any added strategy: your yes, then the replay over past prices and daily paper trading.
"""

import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.researchdesk")

WEEK_KEY = "research:week"
ARXIV = ("q-fin.PM", "q-fin.TR", "q-fin.ST")  # portfolio management, trading, statistical finance
LOOKS_TESTABLE = re.compile(r"\b(trading (?:strateg|rule)|momentum|trend[- ]following|mean[- ]reversion|reversal|"
                            r"anomal|factor (?:invest|timing|model)|backtest|out[- ]of[- ]sample|portfolio (?:construction|"
                            r"selection)|market timing|cross[- ]section|return predictab|moving average|52[- ]week)", re.I)
CODE = (".py", ".md", ".txt", ".cs", ".rst", ".ipynb")
MAX_QUEUE, MAX_FILE, MAX_NOTEBOOK = 200, 400_000, 6_000_000  # a notebook is bigger: it carries its stored outputs
STARTING_POINTS = [  # public repositories with a stated licence, checked on 16 November 2026
    {"title": "QuantConnect research: from research to production (Apache-2.0)",
     "url": "https://github.com/QuantConnect/Research/tree/master/Research2Production/Python"},
    {"title": "QuantConnect research: analysis notebooks (Apache-2.0)",
     "url": "https://github.com/QuantConnect/Research/tree/master/Analysis"},
    {"title": "Machine Learning for Trading: where strategy ideas come from (MIT)",
     "url": "https://github.com/stefan-jansen/machine-learning-for-trading/tree/main/06_strategy_definition"},
    {"title": "Machine Learning for Trading: financial features (MIT)",
     "url": "https://github.com/stefan-jansen/machine-learning-for-trading/tree/main/08_financial_features"},
]
SCHEMA = ["CREATE SEQUENCE IF NOT EXISTS research_id",
          """CREATE TABLE IF NOT EXISTS research_queue (
    id BIGINT PRIMARY KEY DEFAULT nextval('research_id'), source VARCHAR, title VARCHAR, url VARCHAR UNIQUE,
    day DATE, summary VARCHAR, licence VARCHAR DEFAULT '', status VARCHAR DEFAULT 'new', rule_key VARCHAR DEFAULT '',
    note VARCHAR DEFAULT '', found VARCHAR)"""]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


def _keep(memory: Memory, source: str, title: str, url: str, day: str, summary: str, licence: str = "") -> bool:
    got = _run(memory, "INSERT OR IGNORE INTO research_queue (source, title, url, day, summary, licence, found) VALUES "
                       "(?, ?, ?, ?, ?, ?, ?) RETURNING id",
               (source, title[:240], url[:500], day or None, summary[:1500], licence[:60],
                datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return bool(got)


# --- arXiv ------------------------------------------------------------------------------------------------------
def arxiv_new(memory: Memory, fetch=None, days: int = 10, today=None) -> int:
    """New quantitative-finance papers that look like a testable strategy, queued. Returns how many were new."""
    from defusedxml import ElementTree  # noqa: PLC0415
    _ready(memory)
    today = today or local_now().date()
    query = " OR ".join(f"cat:{c}" for c in ARXIV)
    raw = gateway.call_url(memory, "arxiv", ["https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 80})],
        raw=True, fetch=fetch, purpose="research")
    root = ElementTree.fromstring(raw if isinstance(raw, (bytes, str)) else b"<feed/>")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    since, added = (today - timedelta(days=days)).isoformat(), 0
    for e in root.findall("a:entry", ns):
        link = (e.findtext("a:id", "", ns) or "").replace("http://", "https://")
        title = " ".join((e.findtext("a:title", "", ns) or "").split())
        summary = " ".join((e.findtext("a:summary", "", ns) or "").split())
        day = (e.findtext("a:published", "", ns) or "")[:10]
        if not link.startswith("https://arxiv.org/abs/") or not title or day < since:
            continue
        if LOOKS_TESTABLE.search(title + " " + summary) and _keep(memory, "arXiv", title, link, day, summary, "arXiv"):
            added += 1
    return added


# --- GitHub -----------------------------------------------------------------------------------------------------
def _github_parts(url: str) -> tuple[str, str, str, str]:
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+)(?:/(?:blob|tree)/([\w./ %-]+?))?/?",
                     urllib.parse.unquote(str(url or "").strip()))
    if not m:
        raise ToolError("Give a link to a public GitHub repository, folder or file (https://github.com/owner/name/...).")
    owner, repo, rest = m.group(1), m.group(2).removesuffix(".git"), m.group(3) or ""
    branch, _, path = rest.partition("/")
    return owner, repo, branch, path


def github_add(memory: Memory, url: str, fetch=None) -> dict:
    """Queue a public GitHub file, or the readable files of a folder (at most 40). Needs a stated licence."""
    _ready(memory)
    owner, repo, branch, path = _github_parts(url)
    base = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        lic = gateway.call_url(memory, "github", [base + "/license"], fetch=fetch, purpose="research") or {}
    except gateway.ConnectError as e:
        if e.status == 404:
            lic = {}
        else:
            raise
    spdx = str(((lic or {}).get("license") or {}).get("spdx_id") or "")
    if not spdx or spdx == "NOASSERTION":
        raise ToolError(f"{owner}/{repo} states no licence, so Buddy won't copy from it. If you have the right to use "
                        "it, download the file yourself and upload it.")
    q = "?ref=" + urllib.parse.quote(branch) if branch else ""
    got = gateway.call_url(memory, "github", [f"{base}/contents/{urllib.parse.quote(path)}{q}"], fetch=fetch,
                           purpose="research")
    items = got if isinstance(got, list) else [got]
    added = 0
    for it in items[:200]:
        name = str(it.get("name") or "")
        limit = MAX_NOTEBOOK if name.lower().endswith(".ipynb") else MAX_FILE
        if it.get("type") != "file" or not name.lower().endswith(CODE) or int(it.get("size") or 0) > limit:
            continue
        if added >= 40:
            break
        if _keep(memory, "GitHub", f"{owner}/{repo}: {it.get('path') or name}", str(it.get("html_url") or ""),
                 local_now().date().isoformat(), f"A file in {owner}/{repo} ({spdx} licence).", spdx):
            added += 1
    return {"added": added, "licence": spdx, "looked_at": len(items)}


def _github_file(memory: Memory, url: str, fetch=None) -> tuple[str, bytes]:
    owner, repo, branch, path = _github_parts(url)
    q = "?ref=" + urllib.parse.quote(branch) if branch else ""
    raw = gateway.call_url(memory, "github", [f"https://api.github.com/repos/{owner}/{repo}/contents/"
                                              f"{urllib.parse.quote(path)}{q}"],
                           headers={"Accept": "application/vnd.github.raw+json"}, raw=True, fetch=fetch, purpose="research")
    data = raw if isinstance(raw, bytes) else str(raw).encode()
    name = path.rsplit("/", 1)[-1] or "file.txt"
    return name, data if name.lower().endswith(".ipynb") else data[:MAX_FILE]  # a cut-off notebook cannot be read


# --- the queue --------------------------------------------------------------------------------------------------
def queue(memory: Memory, status: str = "new", limit: int = 60) -> list[dict]:
    _ready(memory)
    rows = _run(memory, "SELECT * FROM research_queue WHERE status = ? ORDER BY day DESC, id DESC LIMIT ?", (status, limit))
    for r in rows:
        r["day"] = str(r["day"]) if r["day"] else ""
    return rows


def view(memory: Memory) -> dict:
    _ready(memory)
    counts = {r["status"]: r["n"] for r in _run(memory, "SELECT status, COUNT(*) AS n FROM research_queue GROUP BY 1")}
    return {"new": queue(memory, "new", 30), "read": queue(memory, "read", 15), "counts": counts,
            "last_search": memory.get(WEEK_KEY) or "", "starting_points": STARTING_POINTS,
            "note": "Fetched by program: arXiv's quantitative finance papers (weekly) and public GitHub files you point "
                    "to, when the repository states a licence. Papers from SSRN, journals, catalogues and books are "
                    "yours to download and upload."}


def dismiss(memory: Memory, item_id: int) -> dict:
    _ready(memory)
    _run(memory, "UPDATE research_queue SET status = 'dismissed' WHERE id = ?", (int(item_id),))
    return view(memory)


async def read(memory: Memory, item_id: int, fetch=None, ask=None, reader=None) -> dict:
    """Fetch one queued item, take its text out in the sandbox, and write its strategy as a proposed rule."""
    import asyncio  # noqa: PLC0415
    from . import docread, stratrules  # noqa: PLC0415
    _ready(memory)
    rows = _run(memory, "SELECT * FROM research_queue WHERE id = ?", (int(item_id),))
    if not rows:
        raise ToolError("That item is not in the research queue.")
    item = rows[0]
    text = ""
    try:
        if item["source"] == "arXiv":
            pdf = item["url"].replace("https://arxiv.org/abs/", "https://export.arxiv.org/pdf/")
            raw = await asyncio.to_thread(lambda: gateway.call_url(memory, "arxiv", [pdf], raw=True, fetch=fetch,
                                                                   purpose="research"))
            got = await (reader or docread.read)("paper.pdf", raw if isinstance(raw, bytes) else str(raw).encode())
        else:
            name, data = await asyncio.to_thread(_github_file, memory, item["url"], fetch)
            got = await (reader or docread.read)(name if "." in name else name + ".txt", data)
        text = got["text"]
    except ToolError:
        raise
    except Exception as e:
        log.info("research: could not fetch %s (%s); using its summary", item["url"], type(e).__name__)
    if len(text) < 400:  # the file could not be fetched or read: the abstract is what there is
        text = f"{item['title']}\n\n{item['summary']}"
    try:
        rule = await stratrules.propose(memory, item["title"][:110], text, ask, source_url=item["url"],
                                        licence=item["licence"])
    except ToolError as e:
        _run(memory, "UPDATE research_queue SET status = 'read', note = ? WHERE id = ?", (str(e)[:300], item["id"]))
        raise
    _run(memory, "UPDATE research_queue SET status = 'read', rule_key = ?, note = '' WHERE id = ?", (rule["key"], item["id"]))
    return rule


def due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    week = f"{now.isocalendar()[0]}-{now.isocalendar()[1]:02d}"
    return now.weekday() == 6 and now.hour >= 9 and (memory.get(WEEK_KEY) or "")[:7] != week


def weekly(memory: Memory, fetch=None, now=None) -> str:
    """The Sunday search. Returns a line to tell the owner, or "" when nothing new was found."""
    now = now or local_now()
    memory.set(WEEK_KEY, f"{now.isocalendar()[0]}-{now.isocalendar()[1]:02d} ({now.date().isoformat()})")
    added = arxiv_new(memory, fetch, today=now.date())
    _run(memory, "DELETE FROM research_queue WHERE status = 'dismissed' AND id NOT IN (SELECT id FROM research_queue "
                 "ORDER BY id DESC LIMIT ?)", (MAX_QUEUE,))
    return (f"📚 {added} new research paper{'s' if added != 1 else ''} that may hold a testable strategy "
            "are in the research queue (Money, Invest, Trading). Nothing is read or traded until you pick one.") if added else ""
