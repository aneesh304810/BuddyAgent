"""The research digest: new studies on the health topics the owner chooses, strongest evidence first, from PubMed.

The owner picks topics on the Health page (Learn → Research): ready-made ones (longevity research from Nature Aging,
Aging Cell, GeroScience, the National Institute on Aging, the Buck Institute and AFAR; South Asian heart and metabolic
health; prediabetes; sleep; exercise and ageing; supplements) and up to five of their own words. Once a week (Sunday
night), or when they press Refresh, Buddy asks the National Library of Medicine's E-utilities for each topic's papers
from the last 30 days (90 the first time), by default only guidelines, meta-analyses, systematic reviews and randomised
trials, and leaves out studies only in animals. Each paper is shown with its journal, date, first author and evidence
type, guidelines first, then reviews, then trials, then other studies; its abstract is fetched when the owner opens
it. Code only: no AI model reads or summarises any of it, and no notification is sent (a page, not a push).

Only the topic words go to NCBI (with tool=buddy and no email or key); papers are kept 180 days, the topics until
changed. Research isn't advice: one study rarely changes what anyone should do.
"""

import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET  # nosec B405: entity declarations are refused before parsing (see abstract)
from datetime import timedelta

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

NAME = "pubmed"
TOPICS_KEY, RAN_KEY = "evidence:topics", "evidence:ran"
KEEP_DAYS, WINDOW_DAYS, FIRST_DAYS, PER_TOPIC = 180, 30, 90, 15
PRESETS = {  # key -> (what the owner sees, the PubMed query)
    "longevity": ("Longevity research (Nature Aging, Aging Cell, GeroScience; NIA, Buck Institute, AFAR)",
                  '("Nature aging"[Journal] OR "Aging cell"[Journal] OR "GeroScience"[Journal] OR '
                  '"National Institute on Aging"[Affiliation] OR "Buck Institute"[Affiliation] OR '
                  '"American Federation for Aging Research"[Affiliation])'),
    "south_asian": ("South Asian heart and metabolic health",
                    '("South Asian"[tiab] OR "South Asians"[tiab] OR "Asian Indian"[tiab] OR "Asian Indians"[tiab]) '
                    'AND (cardiovascular[tiab] OR diabetes[tiab] OR "insulin resistance"[tiab] OR lipoprotein[tiab])'),
    "prediabetes": ("Prediabetes: food, exercise and prevention",
                    'prediabetes[tiab] AND (diet[tiab] OR exercise[tiab] OR lifestyle[tiab] OR prevention[tiab])'),
    "sleep": ("Sleep and health", 'sleep[ti] AND (health[tiab] OR cardiovascular[tiab] OR metabolic[tiab] OR '
                                  'cognitive[tiab])'),
    "exercise": ("Exercise and healthy ageing", '("physical activity"[ti] OR exercise[ti] OR "resistance training"[ti]) '
                                                'AND (aging[tiab] OR ageing[tiab] OR mortality[tiab] OR longevity[tiab])'),
    "supplements": ("Supplements (vitamin D, omega-3, magnesium, creatine)",
                    '("vitamin D"[ti] OR "omega-3"[ti] OR "fish oil"[ti] OR magnesium[ti] OR creatine[ti])'),
}
STRONG = ('(practice guideline[pt] OR guideline[pt] OR meta-analysis[pt] OR systematic review[pt] OR '
          'randomized controlled trial[pt])')
PEOPLE = 'NOT (animals[mh] NOT humans[mh])'  # leaves out animal-only studies, keeps those not yet indexed
EVIDENCE = [  # rank, what the owner sees, the publication types that make it
    (0, "Guideline", {"Practice Guideline", "Guideline", "Consensus Development Conference"}),
    (1, "Meta-analysis or systematic review", {"Meta-Analysis", "Network Meta-Analysis", "Systematic Review"}),
    (2, "Randomised trial", {"Randomized Controlled Trial", "Equivalence Trial", "Pragmatic Clinical Trial"}),
    (3, "Study", {"Clinical Trial", "Observational Study", "Multicenter Study", "Comparative Study",
                  "Clinical Trial, Phase III", "Clinical Trial, Phase II"}),
    (4, "Review", {"Review"}),
]
SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_papers (          -- public PubMed records found for the owner's topics
    topic TEXT NOT NULL, pmid TEXT NOT NULL, title TEXT NOT NULL, journal TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '', sort_date TEXT NOT NULL DEFAULT '', types TEXT NOT NULL DEFAULT '[]',
    rank INTEGER NOT NULL, evidence TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '',
    found TEXT NOT NULL, PRIMARY KEY (topic, pmid)
);
"""


def _ready(memory: Memory) -> None:
    memory.db.executescript(SCHEMA)


def _words(text: str) -> str:
    """The owner's own topic, as plain words (no PubMed syntax can be slipped in)."""
    return " ".join(re.sub(r"[^\w\s'-]", " ", str(text or "")).split())[:80]


def settings(memory: Memory) -> dict:
    try:
        got = json.loads(memory.get(TOPICS_KEY) or "{}")
    except ValueError:
        got = {}
    got = got if isinstance(got, dict) else {}
    return {"presets": [p for p in got.get("presets") or [] if p in PRESETS],
            "custom": [c for c in got.get("custom") or [] if _words(c)][:5],
            "strong": got.get("strong", True) is not False, "people": got.get("people", True) is not False}


def choose(memory: Memory, presets: list, custom: list, strong: bool = True, people: bool = True) -> dict:
    words = []
    for c in custom or []:
        w = _words(c)
        if len(w) >= 3 and w.lower() not in [x.lower() for x in words]:
            words.append(w)
    if len(words) > 5:
        raise ToolError("Up to five topics of your own.")
    chosen = {"presets": [p for p in presets or [] if p in PRESETS], "custom": words, "strong": bool(strong),
              "people": bool(people)}
    memory.set(TOPICS_KEY, json.dumps(chosen))
    _ready(memory)
    keep = list(chosen["presets"]) + [f"own:{w.lower()}" for w in words]
    marks = ",".join("?" * len(keep)) or "''"
    memory.db.execute(f"DELETE FROM evidence_papers WHERE topic NOT IN ({marks})", keep)  # nosec B608: marks only
    memory.db.commit()
    return chosen


def _topics(s: dict) -> list[tuple[str, str, str]]:
    """(key, label, query) for each chosen topic."""
    out = [(p, PRESETS[p][0], PRESETS[p][1]) for p in s["presets"]]
    out += [(f"own:{c.lower()}", c, f"({c})") for c in s["custom"]]
    return out


def query(base: str, s: dict) -> str:
    return " AND ".join([base] + ([STRONG] if s["strong"] else [])) + (f" {PEOPLE}" if s["people"] else "")


def rank(types: list[str]) -> tuple[int, str]:
    for r, label, names in EVIDENCE:
        if set(types) & names:
            return r, label
    return 5, "Other"


def _call(memory: Memory, path: str, env=None, fetch=None, raw: bool = False):
    got = gateway.call(memory, NAME, path, env=env, fetch=fetch, raw=raw, purpose="research digest")
    time.sleep(0 if fetch else 0.4)  # NCBI asks for at most 3 requests a second without a key
    return got


def _sort_date(pubdate: str, fallback: str) -> str:
    m = re.match(r"(\d{4})(?:\s+(\w{3}))?(?:\s+(\d{1,2}))?", pubdate or "")
    if not m:
        return fallback
    months = "JanFebMarAprMayJunJulAugSepOctNovDec"
    mon = months.find(m.group(2)) // 3 + 1 if m.group(2) and m.group(2) in months else 1
    return f"{m.group(1)}-{mon:02d}-{int(m.group(3) or 1):02d}"


def refresh(memory: Memory, env=None, fetch=None) -> dict:
    """Ask PubMed for each topic's new papers and keep them."""
    s = settings(memory)
    _ready(memory)
    first = not memory.get(RAN_KEY)
    days = FIRST_DAYS if first else WINDOW_DAYS
    today = local_now().date().isoformat()
    found = new = 0
    for key, _, base in _topics(s):
        ids = ((_call(memory, "/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
            {"db": "pubmed", "retmode": "json", "retmax": PER_TOPIC, "sort": "pub_date", "datetype": "pdat",
             "reldate": days, "tool": "buddy", "term": query(base, s)}), env=env, fetch=fetch) or {})
            .get("esearchresult") or {}).get("idlist") or []
        ids = [i for i in ids if str(i).isdigit()]
        if not ids:
            continue
        result = (_call(memory, "/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
            {"db": "pubmed", "retmode": "json", "tool": "buddy", "id": ",".join(ids)}), env=env, fetch=fetch)
            or {}).get("result") or {}
        for pmid in ids:
            r = result.get(pmid) or {}
            title = " ".join(re.sub(r"<[^>]+>", "", str(r.get("title") or "")).split())
            if not title:
                continue
            types = [str(t) for t in r.get("pubtype") or []]
            rk, label = rank(types)
            doi = next((a.get("value") for a in r.get("articleids") or [] if a.get("idtype") == "doi"), "")
            author = ((r.get("authors") or [{}])[0] or {}).get("name") or ""
            held = memory.db.execute("SELECT 1 FROM evidence_papers WHERE topic = ? AND pmid = ?", (key, pmid)).fetchone()
            memory.db.execute(
                "INSERT INTO evidence_papers (topic, pmid, title, journal, published, sort_date, types, rank, evidence, "
                "author, doi, found) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(topic, pmid) DO UPDATE "
                "SET title = excluded.title, types = excluded.types, rank = excluded.rank, evidence = excluded.evidence",
                (key, pmid, title[:400], str(r.get("fulljournalname") or r.get("source") or "")[:160],
                 str(r.get("pubdate") or "")[:40], _sort_date(str(r.get("pubdate") or ""), today), json.dumps(types),
                 rk, label, str(author)[:80], str(doi)[:120], today))
            found += 1
            new += held is None
    memory.db.execute("DELETE FROM evidence_papers WHERE found < ?",
                      ((local_now().date() - timedelta(days=KEEP_DAYS)).isoformat(),))
    memory.db.commit()
    memory.set(RAN_KEY, today)
    return {"found": found, "new": new, "days": days}


def refresh_now(memory: Memory, env=None, fetch=None) -> dict:
    """The owner's Refresh: at most once an hour (NCBI is a shared public service)."""
    last = float(memory.get("evidence:manual") or 0)
    if time.time() - last < 3600:
        raise ToolError("Refreshed within the hour: try again later.")
    memory.set("evidence:manual", str(time.time()))
    return refresh(memory, env=env, fetch=fetch)


def due(memory: Memory) -> bool:
    """Once a week, on Sunday night, when there are topics."""
    s = settings(memory)
    if not (s["presets"] or s["custom"]):
        return False
    ran = memory.get(RAN_KEY) or ""
    now = local_now().date()
    return now.weekday() == 6 and ran != now.isoformat()


def view(memory: Memory) -> dict:
    s = settings(memory)
    _ready(memory)
    week_ago = (local_now().date() - timedelta(days=7)).isoformat()
    topics = []
    for key, label, _ in _topics(s):
        rows = [dict(r) for r in memory.db.execute(
            "SELECT pmid, title, journal, published, rank, evidence, author, doi, found FROM evidence_papers "
            "WHERE topic = ? ORDER BY rank, sort_date DESC LIMIT 25", (key,))]
        for r in rows:
            r["new"] = r["found"] >= week_ago
            r["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/"
        topics.append({"key": key, "label": label, "papers": rows})
    return {"settings": s, "presets": {k: v[0] for k, v in PRESETS.items()}, "topics": topics,
            "ran": memory.get(RAN_KEY) or "",
            "note": "From PubMed (the US National Library of Medicine). Strongest evidence first: guidelines, then "
                    "meta-analyses and systematic reviews, then randomised trials. Research isn't advice: one study "
                    "rarely changes what you should do; talk to your doctor before acting on it. Only your topic "
                    "words are sent; no AI model reads or summarises any of it."}


def abstract(memory: Memory, pmid: str, env=None, fetch=None) -> dict:
    """A paper's abstract, by section, when the owner opens it."""
    if not re.fullmatch(r"\d{1,9}", str(pmid or "")):
        raise ToolError("That isn't a PubMed number.")
    raw = _call(memory, "/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "retmode": "xml", "rettype": "abstract", "tool": "buddy", "id": pmid}), env=env, fetch=fetch,
        raw=True)
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    text = re.sub(r"<!DOCTYPE PubmedArticleSet PUBLIC \"[^\"<>\[\]]*\" \"[^\"<>\[\]]*\">", "", text, count=1)
    if "<!ENTITY" in text or "<!DOCTYPE" in text:  # never parse entity declarations
        raise ToolError("PubMed sent something unexpected.")
    root = ET.fromstring(text)  # nosec B314: checked above, no entities or doctype
    parts = [{"label": (a.get("Label") or "").capitalize(), "text": " ".join("".join(a.itertext()).split())}
             for a in root.iter("AbstractText")]
    return {"pmid": pmid, "sections": [p for p in parts if p["text"]],
            "note": "Abstracts may be copyrighted by their publishers; shown to you for your own reading."}
