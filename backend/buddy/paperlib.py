"""The paper library: every research paper Buddy finds, from any source, kept with its details and abstract until
the owner deletes it, for future reference and for Buddy's own models.

Where papers come from: PubMed (the health research digest and the freshness watch), arXiv (the research desk,
trends, the tech radar, Buddy Intelligence), Europe PMC and OpenAlex (Buddy Intelligence). Each source keeps its
own short list for its own page; this keeps them all, once each, with which feature and topic found it.

A paper is known by its PubMed number, DOI, arXiv number or OpenAlex id (in that order), worked out from what the
source gives or from its link, so the same paper found twice is one row. Public records only; what's personal in
here is which of the owner's topics found each paper, and that stays in the owner's encrypted database. Nothing is
sent anywhere from here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .memory import Memory
from .tools import ToolError

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (               -- every research paper Buddy found, kept until the owner deletes it
    key TEXT PRIMARY KEY,                         -- pmid:123, doi:10.x/y, arxiv:2401.01234, openalex:W1, url:...
    title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '', venue TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '', abstract TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
    pmid TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '', arxiv TEXT NOT NULL DEFAULT '',
    kinds TEXT NOT NULL DEFAULT '[]', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_finds (          -- which feature found it, and why (a topic, a search)
    key TEXT NOT NULL, source TEXT NOT NULL, why TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (key, source, why)
);
"""
SOURCES = ("PubMed", "arXiv", "Europe PMC", "OpenAlex")
ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I)
PUBMED = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})")
EPMC_MED = re.compile(r"europepmc\.org/(?:article|abstract)/MED/(\d{1,9})", re.I)
DOI = re.compile(r"(?:doi\.org/|^doi:\s*|^)(10\.\d{4,9}/\S+)$", re.I)
OPENALEX = re.compile(r"openalex\.org/(W\d+)|^(?:oa:)?(W\d+)$", re.I)


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _clean(v, n: int) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", str(v or "")).split())[:n]


def ids(url: str = "", pmid: str = "", doi: str = "", arxiv: str = "", openalex: str = "") -> dict:
    """The identifiers a paper has, from what was given and from its link."""
    url = str(url or "").strip()
    out = {"pmid": str(pmid or "").strip(), "doi": str(doi or "").strip().lower(),
           "arxiv": str(arxiv or "").strip(), "openalex": str(openalex or "").strip()}
    if not out["pmid"] and (m := PUBMED.search(url) or EPMC_MED.search(url)):
        out["pmid"] = m.group(1)
    if not out["arxiv"] and (m := ARXIV.search(url)):
        out["arxiv"] = m.group(1)
    if not out["doi"] and (m := DOI.search(url)):
        out["doi"] = m.group(1).lower()
    if out["openalex"] or (m := OPENALEX.search(url)):
        m2 = OPENALEX.search(out["openalex"] or url)
        out["openalex"] = (m2.group(1) or m2.group(2)).upper() if m2 else ""
    out["pmid"] = out["pmid"] if out["pmid"].isdigit() else ""
    out["arxiv"] = re.sub(r"v\d+$", "", out["arxiv"])
    return out


def key_for(i: dict, url: str = "") -> str:
    for kind in ("pmid", "doi", "arxiv", "openalex"):
        if i.get(kind):
            return f"{kind}:{i[kind]}"
    return f"url:{url}" if url.startswith("https://") else ""


def keep(memory: Memory, title: str, source: str, why: str = "", url: str = "", authors: str = "",
         venue: str = "", published: str = "", abstract: str = "", kinds=(), pmid: str = "", doi: str = "",
         arxiv: str = "", openalex: str = "", commit: bool = True) -> str:
    """Keep one paper (or add what's new about one already kept). Returns its key, '' when it can't be known."""
    title = _clean(title, 500)
    found = ids(url, pmid, doi, arxiv, openalex)
    key = key_for(found, str(url or ""))
    if not title or not key:
        return ""
    _ready(memory)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not url:
        url = (f"https://pubmed.ncbi.nlm.nih.gov/{found['pmid']}/" if found["pmid"] else
               f"https://arxiv.org/abs/{found['arxiv']}" if found["arxiv"] else
               f"https://doi.org/{found['doi']}" if found["doi"] else "")
    row = {"title": title, "authors": _clean(authors, 300), "venue": _clean(venue, 200),
           "published": _clean(published, 40), "abstract": _clean(abstract, 8000),
           "url": str(url or "")[:500] if str(url or "").startswith("https://") else "",
           "pmid": found["pmid"], "doi": found["doi"][:200], "arxiv": found["arxiv"],
           "kinds": json.dumps([str(k)[:60] for k in kinds][:12])}
    # a new detail fills a blank; nothing already kept is overwritten with less
    memory.db.execute(
        "INSERT INTO papers (key, title, authors, venue, published, abstract, url, pmid, doi, arxiv, kinds, first_seen, "
        "last_seen) VALUES (:key, :title, :authors, :venue, :published, :abstract, :url, :pmid, :doi, :arxiv, :kinds, "
        ":now, :now) ON CONFLICT(key) DO UPDATE SET last_seen = :now, "
        "authors = CASE WHEN papers.authors = '' THEN :authors ELSE papers.authors END, "
        "venue = CASE WHEN papers.venue = '' THEN :venue ELSE papers.venue END, "
        "published = CASE WHEN papers.published = '' THEN :published ELSE papers.published END, "
        "abstract = CASE WHEN length(:abstract) > length(papers.abstract) THEN :abstract ELSE papers.abstract END, "
        "url = CASE WHEN papers.url = '' THEN :url ELSE papers.url END, "
        "pmid = CASE WHEN papers.pmid = '' THEN :pmid ELSE papers.pmid END, "
        "doi = CASE WHEN papers.doi = '' THEN :doi ELSE papers.doi END, "
        "arxiv = CASE WHEN papers.arxiv = '' THEN :arxiv ELSE papers.arxiv END, "
        "kinds = CASE WHEN papers.kinds = '[]' THEN :kinds ELSE papers.kinds END",
        {"key": key, "now": now, **row})
    memory.db.execute("INSERT OR IGNORE INTO paper_finds (key, source, why, at) VALUES (?, ?, ?, ?)",
                      (key, _clean(source, 40), _clean(why, 200), now))
    if commit:
        memory.db.commit()
    return key


def keep_items(memory: Memory, items, source: str, why: str = "") -> int:
    """Keep a source's list as it already is ({title, url, day/published, text/summary, who/venue}); never fails
    the feature that found them. Returns how many were kept."""
    n = 0
    try:
        for it in items or []:
            if isinstance(it, dict) and keep(
                    memory, it.get("title") or "", source, why, url=it.get("url") or "",
                    venue=it.get("who") or it.get("venue") or it.get("journal") or "",
                    published=it.get("day") or it.get("published") or it.get("date") or "",
                    abstract=it.get("text") or it.get("summary") or it.get("abstract") or "",
                    pmid=it.get("pmid") or "", doi=it.get("doi") or "", commit=False):
                n += 1
        memory.db.commit()
    except Exception:  # noqa: BLE001  the library is a copy: a bad record must not break the page that found it
        return n
    return n


def set_abstract(memory: Memory, key: str, abstract: str) -> None:
    _ready(memory)
    memory.db.execute("UPDATE papers SET abstract = ? WHERE key = ? AND length(abstract) < length(?)",
                      (_clean(abstract, 8000), key, _clean(abstract, 8000)))
    memory.db.commit()


def search(memory: Memory, q: str = "", source: str = "", limit: int = 30, offset: int = 0) -> dict:
    """Papers kept, newest first, matching words in the title, abstract, journal or authors, and a source."""
    _ready(memory)
    words = [w for w in re.findall(r"[\w-]{2,}", str(q or "").lower())][:6]
    where, args = [], []
    for w in words:
        where.append("(lower(p.title) LIKE ? OR lower(p.abstract) LIKE ? OR lower(p.venue) LIKE ? OR "
                     "lower(p.authors) LIKE ?)")
        args += [f"%{w}%"] * 4
    if source:
        if source not in SOURCES:
            raise ToolError("That isn't one of the paper sources.")
        where.append("EXISTS (SELECT 1 FROM paper_finds f WHERE f.key = p.key AND f.source = ?)")
        args.append(source)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    total = memory.db.execute(f"SELECT COUNT(*) FROM papers p {clause}", args).fetchone()[0]  # nosec B608: fixed parts
    rows = [dict(r) for r in memory.db.execute(
        f"SELECT * FROM papers p {clause} ORDER BY p.first_seen DESC, p.key LIMIT ? OFFSET ?",  # nosec B608
        [*args, limit, offset])]
    for r in rows:
        r["kinds"] = json.loads(r["kinds"] or "[]")
        r["found_by"] = [dict(f) for f in memory.db.execute(
            "SELECT source, why, at FROM paper_finds WHERE key = ? ORDER BY at, rowid", (r["key"],))]
    counts = {s: memory.db.execute("SELECT COUNT(DISTINCT key) FROM paper_finds WHERE source = ?", (s,)).fetchone()[0]
              for s in SOURCES}
    return {"total": total, "papers": rows, "counts": counts,
            "all": memory.db.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
            "note": "Every paper Buddy found, from every source, kept until you delete it. Public records; which "
                    "of your topics found each stays on your server."}


def forget(memory: Memory, key: str = "") -> int:
    """Delete one paper, or (no key) the whole library."""
    _ready(memory)
    if key:
        memory.db.execute("DELETE FROM paper_finds WHERE key = ?", (key,))
        n = memory.db.execute("DELETE FROM papers WHERE key = ?", (key,)).rowcount
    else:
        memory.db.execute("DELETE FROM paper_finds")
        n = memory.db.execute("DELETE FROM papers").rowcount
    memory.db.commit()
    return n
