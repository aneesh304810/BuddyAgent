"""Buddy Intelligence Platform: where its evidence comes from.

Every source is free, official or first-hand, and read through Buddy Connect (gateway.py), so each call is
rate-limited, logged and can be switched off. Only a technology's search phrase is sent; nothing about the owner.

  research     arXiv (preprints) and Europe PMC (biomedical papers): work published by its authors
  trials       ClinicalTrials.gov: registered clinical studies, with sponsor and phase
  filings      SEC EDGAR full-text search: company filings (8-K, 10-K, S-1) that mention the technology
  awards       USAspending.gov: US federal contracts and grants that mention it, with recipient and amount
  code         GitHub: new public repositories about it
  discussion   Hacker News: stories about it that drew real attention
  news         GDELT: news coverage of it worldwide (the report's title, outlet and date)
  research     OpenAlex: scholarly works across every field, beyond the two sources above
  grants       NIH RePORTER, NSF and SBIR.gov: US research grants, including those to small companies
  funding      SEC Form D: a private company's own notice that it is raising money

What each kind of evidence is worth is fixed here, in EVIDENCE, and shown beside every item: an official record
is a fact that the record exists; a paper is its authors' claim; a filing is the company's own statement; a
story is a report. None of it is Buddy's opinion.

Not used: paid databases (Crunchbase, PitchBook, CB Insights) and anything that would need scraping.
A source that fails is recorded and skipped; the others still run.
"""

import json
import logging
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from . import gateway

log = logging.getLogger(__name__)

EVIDENCE = {
    "research": ("published research", "its authors' claim; not yet proven in use"),
    "trial": ("official record", "a registered clinical study exists; it says nothing about the result"),
    "filing": ("company disclosure", "the company's own statement in a filing with the SEC"),
    "award": ("official record", "a US federal contract or grant was awarded"),
    "grant": ("official record", "a US research grant was awarded; funded research, not a product"),
    "funding": ("company disclosure", "a private company told the SEC it is raising money; the terms are in the filing"),
    "code": ("code published", "working code exists in public; not proof anyone uses it"),
    "news": ("reported", "a news report; check the outlet and what it cites"),
    "discussion": ("reported", "a story people discussed; check the source it links to"),
}
KINDS = tuple(EVIDENCE)
# How far a record can be relied on: that the record exists is verified for official records and filings; what a
# paper or a news story says is reported. What the platform works out from many records is inferred, and what
# an AI writes about them is interpretation. One never silently becomes another.
STANDING = {"trial": "verified", "award": "verified", "grant": "verified", "filing": "verified", "funding": "verified",
            "code": "verified", "research": "reported", "news": "reported", "discussion": "reported"}
STANDINGS = {"verified": "an official record or filing exists (what it claims is its author's)",
             "reported": "published by its authors or a third party; not independently confirmed",
             "inferred": "worked out by fixed rules from several records (stages, convergences, formations)",
             "interpretation": "written by an AI from record titles; not evidence"}
SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|llc|l\.l\.c|ltd|limited|plc|lp|llp|holdings?|group|"
                      r"the|sa|ag|nv|gmbh)\b\.?", re.I)


def org_key(name: str) -> str:
    """One key for the same organisation written different ways: "Oklo Inc. (OKLO)" and "OKLO INC" match."""
    text = re.sub(r"\([^)]*\)", " ", str(name or "").lower())
    text = SUFFIXES.sub(" ", re.sub(r"[^a-z0-9& ]", " ", text))
    return " ".join(text.split())
STATUS_PREFIX = "bip:source:"


def _get(memory, name: str, url: str, fetch=None, headers=None, data=None, raw=False):
    return gateway.call_url(memory, name, [url], data=data, headers=headers or {}, raw=raw, fetch=fetch,
                            purpose="intelligence")


def _day(text) -> str:
    found = re.search(r"\d{4}-\d{2}-\d{2}", str(text or ""))
    if found:
        return found.group(0)
    month = re.fullmatch(r"(\d{4})-(\d{2})", str(text or "").strip())
    return f"{month.group(1)}-{month.group(2)}-01" if month else ""


def arxiv(memory, phrase: str, fetch=None) -> list[dict]:
    from defusedxml import ElementTree  # noqa: PLC0415
    raw = _get(memory, "arxiv", "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": f'all:"{phrase}"', "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 20}),
        fetch, raw=True)
    root = ElementTree.fromstring(raw if isinstance(raw, (bytes, str)) else b"<feed/>")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        link = e.findtext("a:id", "", ns) or ""
        out.append({"key": link, "kind": "research", "source": "arXiv",
                    "title": " ".join((e.findtext("a:title", "", ns) or "").split()), "url": link.replace("http://", "https://"),
                    "day": _day(e.findtext("a:published", "", ns)), "who": "", "amount": None,
                    "text": (e.findtext("a:summary", "", ns) or "")[:1500]})
    return out


def europepmc(memory, phrase: str, fetch=None) -> list[dict]:
    got = _get(memory, "europepmc", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
        {"query": f'"{phrase}"', "format": "json", "pageSize": 20, "sort": "P_PDATE_D desc", "resultType": "lite"}), fetch)
    out = []
    for r in ((got or {}).get("resultList") or {}).get("result") or []:
        if not r.get("id") or not r.get("title"):
            continue
        out.append({"key": f"epmc:{r.get('source')}:{r['id']}", "kind": "research", "source": "Europe PMC",
                    "title": " ".join(str(r["title"]).split()), "day": _day(r.get("firstPublicationDate")),
                    "url": f"https://europepmc.org/article/{r.get('source', 'MED')}/{r['id']}",
                    "who": str(r.get("journalTitle") or "")[:80], "amount": None, "text": ""})
    return out


def trials(memory, phrase: str, fetch=None) -> list[dict]:
    got = _get(memory, "clinicaltrials", "https://clinicaltrials.gov/api/v2/studies?" + urllib.parse.urlencode(
        {"query.term": phrase, "pageSize": 20, "sort": "LastUpdatePostDate:desc"}), fetch)
    out = []
    for s in (got or {}).get("studies") or []:
        p = s.get("protocolSection") or {}
        ident, status = p.get("identificationModule") or {}, p.get("statusModule") or {}
        nct = ident.get("nctId")
        if not nct:
            continue
        phases = ", ".join((p.get("designModule") or {}).get("phases") or [])
        out.append({"key": f"nct:{nct}", "kind": "trial", "source": "ClinicalTrials.gov",
                    "title": " ".join(str(ident.get("briefTitle") or nct).split())
                             + (f" ({phases.replace('PHASE', 'phase ').lower()})" if phases else ""),
                    "url": f"https://clinicaltrials.gov/study/{nct}",
                    "day": _day((status.get("startDateStruct") or {}).get("date"))
                           or _day((status.get("lastUpdatePostDateStruct") or {}).get("date")),
                    "who": str(((p.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name") or "")[:120],
                    "amount": None, "text": str(status.get("overallStatus") or "")})
    return out


def filings(memory, phrase: str, fetch=None, env=None, today: date | None = None) -> list[dict]:
    from .funds import _headers  # noqa: PLC0415  (the contact email SEC asks for in each request)
    today = today or datetime.now(timezone.utc).date()
    got = _get(memory, "sec_search", "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(
        {"q": f'"{phrase}"', "forms": "8-K,10-K,S-1", "dateRange": "custom",
         "startdt": (today - timedelta(days=120)).isoformat(), "enddt": today.isoformat()}), fetch, headers=_headers(env))
    out = []
    for h in ((got or {}).get("hits") or {}).get("hits") or []:
        src = h.get("_source") or {}
        names = src.get("display_names") or []
        if not names or not h.get("_id"):
            continue
        name = re.sub(r"\s*\(CIK \d+\)", "", str(names[0])).strip()
        accession, _, document = str(h["_id"]).partition(":")
        cik = str((src.get("ciks") or [""])[0]).lstrip("0")
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
               if cik and document else "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany")
        out.append({"key": f"sec:{h['_id']}", "kind": "filing", "source": "SEC EDGAR",
                    "title": f"{name}: {src.get('form') or src.get('file_type') or 'filing'} mentions {phrase}",
                    "url": url, "day": _day(src.get("file_date")), "who": name[:120], "amount": None, "text": ""})
    return out


def awards(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    body = {"filters": {"keywords": [phrase], "award_type_codes": ["A", "B", "C", "D", "02", "03", "04", "05"],
                        "time_period": [{"start_date": (today - timedelta(days=365)).isoformat(),
                                         "end_date": today.isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date", "Awarding Agency", "Description",
                       "generated_internal_id"],
            "limit": 20, "page": 1, "sort": "Award Amount", "order": "desc"}
    got = _get(memory, "usaspending", "https://api.usaspending.gov/api/v2/search/spending_by_award/", fetch,
               headers={"Content-Type": "application/json"}, data=json.dumps(body).encode())
    out = []
    for r in (got or {}).get("results") or []:
        award = r.get("Award ID") or r.get("generated_internal_id")
        if not award:
            continue
        amount = r.get("Award Amount")
        out.append({"key": f"usa:{award}", "kind": "award", "source": "USAspending.gov",
                    "title": f"{r.get('Awarding Agency') or 'US government'} award to {r.get('Recipient Name') or 'a recipient'}"
                             + (f": {str(r.get('Description'))[:120]}" if r.get("Description") else ""),
                    "url": (f"https://www.usaspending.gov/award/{r['generated_internal_id']}"
                            if r.get("generated_internal_id") else "https://www.usaspending.gov/"),
                    "day": _day(r.get("Start Date")), "who": str(r.get("Recipient Name") or "")[:120],
                    "amount": float(amount) if isinstance(amount, (int, float)) else None,
                    "text": str(r.get("Description") or "")[:600]})
    return out


def code(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    got = _get(memory, "github", "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": f'"{phrase}" created:>{(today - timedelta(days=90)).isoformat()} stars:>20', "sort": "stars",
         "order": "desc", "per_page": 15}), fetch, headers={"Accept": "application/vnd.github+json"})
    out = []
    for r in (got or {}).get("items") or []:
        if not r.get("html_url"):
            continue
        out.append({"key": r["html_url"], "kind": "code", "source": "GitHub",
                    "title": f"{r.get('full_name')}: {(r.get('description') or '')[:140]}", "url": r["html_url"],
                    "day": _day(r.get("created_at")), "who": str((r.get("owner") or {}).get("login") or "")[:80],
                    "amount": float(r.get("stargazers_count") or 0), "text": str(r.get("description") or "")[:400]})
    return out


def discussion(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    since = int(time.time()) - 30 * 86400
    got = _get(memory, "hackernews", "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(
        {"query": f'"{phrase}"', "tags": "story", "numericFilters": f"points>40,created_at_i>{since}",
         "hitsPerPage": 15}), fetch)
    out = []
    for h in (got or {}).get("hits") or []:
        if not h.get("objectID") or not h.get("title"):
            continue
        link = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append({"key": f"hn:{h['objectID']}", "kind": "discussion", "source": "Hacker News", "title": h["title"][:240],
                    "url": link if str(link).startswith("https://") else f"https://news.ycombinator.com/item?id={h['objectID']}",
                    "day": _day(h.get("created_at")), "who": "", "amount": float(h.get("points") or 0), "text": ""})
    return out


def gdelt(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    got = _get(memory, "gdelt", "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {"query": f'"{phrase}" sourcelang:english', "mode": "ArtList", "format": "json", "maxrecords": 25,
         "timespan": "14d", "sort": "DateDesc"}), fetch)
    out, seen = [], set()
    for a in (got or {}).get("articles") or []:
        url, title = str(a.get("url") or ""), " ".join(str(a.get("title") or "").split())
        if not url.startswith("https://") or not title or title.lower() in seen:
            continue  # the same story syndicated to many outlets counts once
        seen.add(title.lower())
        stamp = str(a.get("seendate") or "")
        out.append({"key": f"gdelt:{url[:280]}", "kind": "news", "source": "GDELT", "title": title[:240], "url": url,
                    "day": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if re.match(r"\d{8}", stamp) else "",
                    "who": str(a.get("domain") or "")[:80], "amount": None, "text": ""})
    return out


def openalex(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    got = _get(memory, "openalex", "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"search": f'"{phrase}"', "filter": f"from_publication_date:{(today - timedelta(days=120)).isoformat()},type:article",
         "sort": "publication_date:desc", "per-page": 25}), fetch)
    out = []
    for w in (got or {}).get("results") or []:
        title = " ".join(str(w.get("title") or w.get("display_name") or "").split())
        if not w.get("id") or not title:
            continue
        link = str(w.get("doi") or w["id"])
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        out.append({"key": f"oa:{str(w['id']).rsplit('/', 1)[-1]}", "kind": "research", "source": "OpenAlex",
                    "title": title[:240], "url": link if link.startswith("https://") else "",
                    "day": _day(w.get("publication_date")), "who": str(venue)[:80], "amount": None, "text": ""})
    return out


def nih(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    body = {"criteria": {"advanced_text_search": {"operator": "and", "search_field": "projecttitle,terms",
                                                  "search_text": f'"{phrase}"'},
                         "fiscal_years": [today.year, today.year - 1]},
            "include_fields": ["ApplId", "ProjectTitle", "Organization", "AwardAmount", "ProjectStartDate",
                               "ProjectDetailUrl", "ProjectNum"],
            "limit": 25, "offset": 0, "sort_field": "project_start_date", "sort_order": "desc"}
    got = _get(memory, "nih_reporter", "https://api.reporter.nih.gov/v2/projects/search", fetch,
               headers={"Content-Type": "application/json"}, data=json.dumps(body).encode())
    out = []
    for r in (got or {}).get("results") or []:
        if not r.get("appl_id") or not r.get("project_title"):
            continue
        amount = r.get("award_amount")
        link = str(r.get("project_detail_url") or f"https://reporter.nih.gov/project-details/{r['appl_id']}")
        out.append({"key": f"nih:{r['appl_id']}", "kind": "grant", "source": "NIH RePORTER",
                    "title": f"NIH grant: {' '.join(str(r['project_title']).split())[:200]}",
                    "url": link if link.startswith("https://") else "", "day": _day(r.get("project_start_date")),
                    "who": str((r.get("organization") or {}).get("org_name") or "")[:120],
                    "amount": float(amount) if isinstance(amount, (int, float)) else None, "text": ""})
    return out


def nsf(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    query = urllib.parse.urlencode({"keyword": f'"{phrase}"', "dateStart": f"{today - timedelta(days=365):%m/%d/%Y}",
                                    "printFields": "id,title,awardeeName,fundsObligatedAmt,startDate", "rpp": 25})
    got = gateway.call_url(memory, "nsf", [f"https://api.nsf.gov/services/v1/awards.json?{query}",
                                           f"https://www.research.gov/awardapi-service/v1/awards.json?{query}"],
                           fetch=fetch, purpose="intelligence")
    out = []
    for a in ((got or {}).get("response") or {}).get("award") or []:
        if not a.get("id") or not a.get("title"):
            continue
        start = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", str(a.get("startDate") or ""))
        try:
            amount = float(a.get("fundsObligatedAmt"))
        except (TypeError, ValueError):
            amount = None
        out.append({"key": f"nsf:{a['id']}", "kind": "grant", "source": "NSF", "title": f"NSF grant: {str(a['title'])[:200]}",
                    "url": f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={a['id']}",
                    "day": f"{start.group(3)}-{start.group(1)}-{start.group(2)}" if start else "",
                    "who": str(a.get("awardeeName") or "")[:120], "amount": amount, "text": ""})
    return out


def sbir(memory, phrase: str, fetch=None, today: date | None = None) -> list[dict]:
    got = _get(memory, "sbir", "https://api.www.sbir.gov/public/api/awards?" + urllib.parse.urlencode(
        {"keyword": phrase, "rows": 25}), fetch)
    out = []
    for a in got if isinstance(got, list) else (got or {}).get("results") or []:
        title, firm = a.get("award_title") or a.get("title"), a.get("firm") or a.get("company")
        if not title or not firm:
            continue
        try:
            amount = float(a.get("award_amount"))
        except (TypeError, ValueError):
            amount = None
        link = str(a.get("award_link") or "")
        ident = a.get("agency_tracking_number") or a.get("contract") or f"{firm}:{title}"[:120]
        out.append({"key": f"sbir:{ident}", "kind": "grant", "source": "SBIR.gov",
                    "title": f"{a.get('agency') or 'US'} small-business grant ({a.get('phase') or 'SBIR'}): {str(title)[:180]}",
                    "url": link if link.startswith("https://") else "https://www.sbir.gov/awards",
                    "day": _day(a.get("proposal_award_date") or a.get("award_date"))
                           or (f"{a['award_year']}-01-01" if str(a.get("award_year") or "").isdigit() else ""),
                    "who": str(firm)[:120], "amount": amount, "text": str(a.get("abstract") or "")[:600]})
    return out


def form_d(memory, phrase: str, fetch=None, env=None, today: date | None = None) -> list[dict]:
    """Private companies' own notices of raising money (Form D) that mention the technology, usually in the
    company's name. Few per technology: most notices don't describe the business."""
    from .funds import _headers  # noqa: PLC0415
    today = today or datetime.now(timezone.utc).date()
    got = _get(memory, "sec_search", "https://efts.sec.gov/LATEST/search-index?" + urllib.parse.urlencode(
        {"q": f'"{phrase}"', "forms": "D,D/A", "dateRange": "custom",
         "startdt": (today - timedelta(days=365)).isoformat(), "enddt": today.isoformat()}), fetch, headers=_headers(env))
    out = []
    for h in ((got or {}).get("hits") or {}).get("hits") or []:
        src = h.get("_source") or {}
        names = src.get("display_names") or []
        if not names or not h.get("_id"):
            continue
        name = re.sub(r"\s*\(CIK \d+\)", "", str(names[0])).strip()
        accession, _, document = str(h["_id"]).partition(":")
        cik = str((src.get("ciks") or [""])[0]).lstrip("0")
        out.append({"key": f"formd:{h['_id']}", "kind": "funding", "source": "SEC Form D",
                    "title": f"{name}: notice of a private fundraising (Form D)",
                    "url": (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
                            if cik and document else "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"),
                    "day": _day(src.get("file_date")), "who": name[:120], "amount": None, "text": ""})
    return out


PAUSE = {"arxiv": 3.5, "gdelt": 5.5}  # seconds these sources ask for between requests
SOURCES = {  # name -> (function, the sectors it is asked about; None = all)
    "arxiv": (arxiv, None), "europepmc": (europepmc, {"biomedical"}), "trials": (trials, {"biomedical"}),
    "filings": (filings, None), "awards": (awards, None),
    "code": (code, {"ai", "cybersecurity", "robotics", "cloud", "infrastructure", "space"}),
    "discussion": (discussion, None),
    "gdelt": (gdelt, None), "openalex": (openalex, None), "nih": (nih, {"biomedical"}), "nsf": (nsf, None),
    "sbir": (sbir, None), "form_d": (form_d, None),
}
KIND_OF = {"arxiv": "research", "europepmc": "research", "trials": "trial", "filings": "filing", "awards": "award",
           "code": "code", "discussion": "discussion", "gdelt": "news", "openalex": "research", "nih": "grant",
           "nsf": "grant", "sbir": "grant", "form_d": "funding"}
TITLES = {"arxiv": "arXiv (research papers)", "europepmc": "Europe PMC (biomedical papers)",
          "trials": "ClinicalTrials.gov (registered studies)", "filings": "SEC EDGAR (company filings)",
          "awards": "USAspending.gov (federal awards)", "code": "GitHub (new code)",
          "discussion": "Hacker News (discussion)", "gdelt": "GDELT (news worldwide)",
          "openalex": "OpenAlex (scholarly works)", "nih": "NIH RePORTER (research grants)",
          "nsf": "NSF (research grants)", "sbir": "SBIR.gov (small-business grants)",
          "form_d": "SEC Form D (private fundraising notices)"}


def note(memory, name: str, ok: bool, detail: str = "", found: int = 0) -> None:
    try:
        last = json.loads(memory.get(STATUS_PREFIX + name) or "{}")
        last.update({"at": datetime.now(timezone.utc).isoformat(), "ok": ok, "detail": detail[:160]})
        if ok:
            last["found_last_run"] = last.get("found_last_run", 0) + found
        memory.set(STATUS_PREFIX + name, json.dumps(last))
    except Exception:
        log.debug("bip: source status not saved", exc_info=True)


def status(memory) -> list[dict]:
    out = []
    for name in SOURCES:
        last = json.loads(memory.get(STATUS_PREFIX + name) or "{}")
        kind = KIND_OF[name]
        out.append({"name": name, "title": TITLES[name], "kind": kind, "evidence": EVIDENCE[kind][0],
                    "means": EVIDENCE[kind][1], "standing": STANDING[kind], "state": "not run yet" if not last.get("at") else "working" if last.get("ok") else "failing",
                    "detail": last.get("detail", ""), "last_run": last.get("at", ""),
                    "found_last_run": last.get("found_last_run", 0)})
    return out
