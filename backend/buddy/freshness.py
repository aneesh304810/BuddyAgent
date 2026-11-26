"""The freshness watch: whether the guidelines and tables Buddy's health rules rest on have a newer version, and
whether Buddy itself has a newer version waiting to be deployed.

Health rules are fixed in code on purpose (exact, tested), so they don't change by themselves. This watch tells the
owner when one may be out of date; it never rewrites a rule. A rule it flags shows as "under review" on the page
until the code is updated (by the owner asking for it, with tests) and deployed. Checked by code, from official
sources only, once a week (Sunday night) or on request:

- **Screenings (US Preventive Services Task Force)**: its own data API (the Prevention TaskForce API) when the owner
  has the key AHRQ grants on request (BUDDY_USPSTF_KEY; BUDDY_USPSTF_URL if AHRQ gives another address). The whole
  data set is fetched, with no age, sex or anything else about the owner, and each recommendation's grade and ages
  are compared with last week's; topics newer than the date Buddy's rules were checked are flagged too. The
  task force's own words are kept, to show verbatim as its terms require. Without the key: PubMed, for new task
  force recommendation statements (published in JAMA).
- **Diabetes rules (ADA Standards of Care)**: PubMed, for a newer yearly edition.
- **Cholesterol and heart-risk rules (ACC/AHA)**: PubMed, for a guideline newer than the 2026 dyslipidemia guideline.
- **Menu dish numbers (USDA FNDDS)**: USDA FoodData Central's API (the owner's key), for a newer survey-food release.
- **Compared with others (CDC NHANES)**: CDC's public data files, for the next survey cycle's demographics file.
- **Diet targets (Dietary Guidelines for Americans 2025-2030)**: the next edition is due in 2030; nothing to watch.
- **Buddy itself**: the server's copy of the code against the repository's branch (git ls-remote, read only; the
  deploy stays the owner's command).

Nothing about the owner is sent anywhere by these checks. No AI model is involved.
"""

import json
import os
import re
import subprocess  # nosec B404 - only fixed git commands, no shell, no user input
import time
import urllib.parse
from pathlib import Path

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

STATE_KEY, MANUAL_KEY, BASELINE_KEY, WORDS_KEY = ("freshness:state", "freshness:manual", "freshness:uspstf_base",
                                                 "freshness:uspstf_words")
UPDATE_KEY = "freshness:update"
ROOT = Path(__file__).resolve().parents[2]
USPSTF_Q = ('Preventive[ti] AND Services[ti] AND "Task Force"[ti] AND Recommendation[ti] AND Statement[ti] AND '
            '"JAMA"[ta]')
ADA_Q = 'Standards[ti] AND Care[ti] AND Diabetes[ti] AND "Diabetes care"[ta]'
LIPIDS_Q = ('(guideline[pt] OR practice guideline[pt]) AND (dyslipidemia[ti] OR cholesterol[ti]) AND '
            '("J Am Coll Cardiol"[ta] OR "Circulation"[ta])')
ADA_EDITION, LIPIDS_EDITION, FNDDS_RELEASE = 2026, 2026, "2024-10-31"
NHANES_NEXT = "/Nchs/Data/Nhanes/Public/2025/DataFiles/DEMO_M.xpt"
TOPICS = {  # a Checkups screening -> words in a task force title that mean it may have changed
    "blood_pressure": r"hypertension|blood pressure", "diabetes": r"prediabetes|type 2 diabetes",
    "cholesterol": r"statin", "colorectal": r"colorectal", "lung": r"lung cancer", "aaa": r"abdominal aortic",
    "breast": r"breast cancer", "cervical": r"cervical cancer", "brca": r"brca", "prostate": r"prostate",
    "bone": r"osteoporosis", "hep_c": r"hepatitis c", "hiv": r"\bhiv\b", "hep_b": r"hepatitis b",
    "tb": r"tuberculosis", "checkup_questions": r"depression|anxiety|unhealthy alcohol|drug use|intimate partner",
    "quit": r"tobacco", "lifestyle": r"healthy diet|physical activity|weight", "falls": r"\bfalls\b",
}


def _checked() -> str:
    from .prevention import CHECKED  # noqa: PLC0415
    return CHECKED


def items_of(title: str) -> list[str]:
    t = title.lower()
    return [k for k, pattern in TOPICS.items() if re.search(pattern, t)]


def _pubmed(memory: Memory, term: str, since: str, env=None, fetch=None, limit: int = 20) -> list[dict]:
    got = gateway.call(memory, "pubmed", "/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "retmode": "json", "retmax": limit, "sort": "pub_date", "datetype": "pdat",
         "mindate": since.replace("-", "/"), "maxdate": "3000", "tool": "buddy", "term": term}), env=env, fetch=fetch,
        purpose="freshness") or {}
    ids = [i for i in (got.get("esearchresult") or {}).get("idlist") or [] if str(i).isdigit()]
    if not ids:
        return []
    result = (gateway.call(memory, "pubmed", "/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "retmode": "json", "tool": "buddy", "id": ",".join(ids)}), env=env, fetch=fetch,
        purpose="freshness") or {}).get("result") or {}
    return [{"pmid": i, "title": " ".join(str((result.get(i) or {}).get("title") or "").split()),
             "date": str((result.get(i) or {}).get("pubdate") or ""),
             "url": f"https://pubmed.ncbi.nlm.nih.gov/{i}/"} for i in ids if (result.get(i) or {}).get("title")]


# --- the watches: each returns {"status": current|changed|not_set_up|unknown, "detail", ...}

def watch_uspstf(memory: Memory, env=None, fetch=None) -> dict:
    checked = _checked()
    if gateway._secret(env, "BUDDY_USPSTF_KEY"):
        return _uspstf_api(memory, env, fetch)
    papers = _pubmed(memory, USPSTF_Q, checked, env, fetch)
    flagged = [p | {"items": items_of(p["title"])} for p in papers]
    mine = [p for p in flagged if p["items"]]
    detail = (f"{len(mine)} new task force statement{'s' if len(mine) != 1 else ''} on screenings Buddy uses, "
              f"since its rules were checked on {checked}." if mine else
              f"No new task force statement on screenings Buddy uses since {checked}.")
    return {"status": "changed" if mine else "current", "detail": detail, "papers": flagged, "via": "PubMed",
            "hint": "For the task force's own data, request the free Prevention TaskForce API key from AHRQ, then "
                    "sudo bash /opt/buddy/deploy/set.sh BUDDY_USPSTF_KEY"}


def _fingerprint(data: dict) -> tuple[dict, dict]:
    """alias -> its year and each specific recommendation's grade, sex and ages; alias -> its verbatim texts."""
    general = data.get("generalRecommendations") or {}
    prints, words = {}, {}
    for spec in data.get("specificRecommendations") or []:
        g = general.get(str(spec.get("general"))) or {}
        alias = str(g.get("uspstfAlias") or g.get("title") or spec.get("title") or "")
        if not alias:
            continue
        entry = prints.setdefault(alias, {"year": str(g.get("topicYear") or ""), "title": _plain(g.get("title")),
                                          "specs": []})
        entry["specs"].append([str(spec.get("grade") or ""), str(spec.get("sex") or ""), spec.get("ageRange")])
        words.setdefault(alias, []).append({"title": _plain(spec.get("title")), "grade": str(spec.get("grade") or ""),
                                            "text": _plain(spec.get("text"))})
    for entry in prints.values():
        entry["specs"].sort(key=json.dumps)
    return prints, words


def _plain(html: str | None) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", str(html or "")).replace("&nbsp;", " ").split())


def _uspstf_api(memory: Memory, env=None, fetch=None) -> dict:
    key = gateway._secret(env, "BUDDY_USPSTF_KEY")
    base = gateway._secret(env, "BUDDY_USPSTF_URL") or gateway.CATALOG["uspstf_api"]["hosts"][0] + "/"
    url = base + ("&" if "?" in base else "?") + urllib.parse.urlencode({"key": key})
    data = gateway.call_url(memory, "uspstf_api", [url], fetch=fetch, purpose="freshness", endpoint="all") or {}
    prints, words = _fingerprint(data)
    if not prints:
        return {"status": "unknown", "detail": "The task force's API answered with no recommendations.", "via": "API"}
    try:
        before = json.loads(memory.get(BASELINE_KEY) or "{}")
    except ValueError:
        before = {}
    year = int(_checked()[:4])
    changed = [a for a, e in prints.items() if (a in before and before[a] != e)
               or (e["year"].isdigit() and int(e["year"]) > year)]
    memory.set(BASELINE_KEY, json.dumps(prints))
    memory.set(WORDS_KEY, json.dumps(words))
    flagged = [{"title": prints[a]["title"] or a, "date": prints[a]["year"], "alias": a,
                "items": sorted(set(items_of(prints[a]["title"] + " " + a)))} for a in changed]
    mine = [f for f in flagged if f["items"]]
    return {"status": "changed" if mine else "current", "via": "the task force's API", "papers": flagged,
            "detail": (f"{len(mine)} recommendation{'s' if len(mine) != 1 else ''} on screenings Buddy uses changed "
                       "in the task force's data." if mine else
                       f"The task force's data matches ({len(prints)} topics); nothing newer than Buddy's rules.")}


def watch_edition(memory: Memory, term: str, since: str, newer, have: str, env=None, fetch=None) -> dict:
    papers = [p for p in _pubmed(memory, term, since, env, fetch) if newer(p["title"])]
    return {"status": "changed" if papers else "current", "via": "PubMed", "papers": papers[:3],
            "detail": f"A newer edition than {have} was published: {papers[0]['title']} ({papers[0]['date']})."
            if papers else f"Nothing newer than {have} on PubMed."}


def _ada_newer(title: str) -> bool:
    m = re.search(r"Standards of Care in Diabetes\W+(20\d\d)", title)
    return bool(m and int(m.group(1)) > ADA_EDITION)


def _lipids_newer(title: str) -> bool:
    m = re.match(r"(20\d\d) .*Guideline on the Management of (Dyslipidemia|Blood Cholesterol)", title)
    return bool(m and int(m.group(1)) > LIPIDS_EDITION)


def watch_fndds(memory: Memory, env=None, fetch=None) -> dict:
    if not gateway._secret(env, "BUDDY_USDA_KEY"):
        return {"status": "not_set_up", "detail": "Needs your USDA key (the same one food lookups use).",
                "hint": "sudo bash /opt/buddy/deploy/set.sh BUDDY_USDA_KEY"}
    got = gateway.call(memory, "usda_fdc", "/fdc/v1/foods/search?" + urllib.parse.urlencode(
        {"query": "idli", "dataType": "Survey (FNDDS)", "pageSize": 1}), env=env, fetch=fetch,
        purpose="freshness") or {}
    published = str(((got.get("foods") or [{}])[0] or {}).get("publishedDate") or "")[:10]
    if not published:
        return {"status": "unknown", "detail": "USDA's answer had no release date."}
    newer = published > FNDDS_RELEASE
    return {"status": "changed" if newer else "current", "via": "USDA's API",
            "detail": f"USDA's survey foods were released {published}; the menus use {FNDDS_RELEASE}."
            if newer else f"The menus use USDA's latest survey foods ({FNDDS_RELEASE})."}


def watch_nhanes(memory: Memory, env=None, fetch=None) -> dict:
    try:
        gateway.call(memory, "nhanes", NHANES_NEXT, env=env, fetch=fetch, raw=True, method="HEAD",
                     purpose="freshness")
    except gateway.ConnectError as e:
        if e.status == 404:
            return {"status": "current", "via": "CDC's data files",
                    "detail": "The next survey cycle's files aren't out yet (looked for its demographics file)."}
        raise
    return {"status": "changed", "via": "CDC's data files",
            "detail": "CDC has published the next survey cycle: the comparisons can be rebuilt with it."}


WATCHES = [  # key, what it covers, what Buddy has now, how it is watched
    ("uspstf", "Screenings (US Preventive Services Task Force)", "rules checked {checked}", watch_uspstf),
    ("ada", "Diabetes rules (ADA Standards of Care)", "Standards of Care in Diabetes 2026",
     lambda m, env=None, fetch=None: watch_edition(m, ADA_Q, "2026-06-01", _ada_newer, "the 2026 Standards",
                                                   env, fetch)),
    ("lipids", "Cholesterol and heart-risk rules (ACC/AHA)", "2026 ACC/AHA dyslipidemia guideline",
     lambda m, env=None, fetch=None: watch_edition(m, LIPIDS_Q, "2026-06-01", _lipids_newer,
                                                   "the 2026 dyslipidemia guideline", env, fetch)),
    ("fndds", "Menu dish numbers (USDA FNDDS)", f"USDA survey foods of {FNDDS_RELEASE}", watch_fndds),
    ("nhanes", "Compared with others (CDC NHANES)", "NHANES 2017-March 2020 and 2021-2023", watch_nhanes),
    ("dga", "Diet targets (Dietary Guidelines for Americans)", "2025-2030 edition (January 2026)",
     lambda m, env=None, fetch=None: {"status": "current", "detail": "The next edition is due in 2030."}),
]


def run(memory: Memory, env=None, fetch=None) -> dict:
    out = {"checked": local_now().isoformat(timespec="minutes"), "watches": []}
    for key, title, have, watch in WATCHES:
        row = {"key": key, "title": title, "have": have.format(checked=_checked())}
        try:
            row |= watch(memory, env=env, fetch=fetch)
        except ToolError as e:
            row |= {"status": "unknown", "detail": f"Couldn't check: {e}"}
        out["watches"].append(row)
        if row.get("papers"):
            from . import paperlib  # noqa: PLC0415  kept in the paper library, beyond this week's list
            paperlib.keep_items(memory, row["papers"], "PubMed", f"Freshness watch: {title}")
    memory.set(STATE_KEY, json.dumps(out))
    return out


def check_now(memory: Memory, env=None, fetch=None, git=None) -> dict:
    """The owner's Check now: at most once an hour."""
    if time.time() - float(memory.get(MANUAL_KEY) or 0) < 3600:
        raise ToolError("Checked within the hour: try again later.")
    memory.set(MANUAL_KEY, str(time.time()))
    run(memory, env=env, fetch=fetch)
    update(memory, git=git)
    return view(memory)


def due(memory: Memory) -> bool:
    """Weekly, on Sunday night (and the first time)."""
    state = _state(memory)
    today = local_now().date()
    return not state or (today.weekday() == 6 and not state.get("checked", "").startswith(today.isoformat()))


def _state(memory: Memory) -> dict:
    try:
        return json.loads(memory.get(STATE_KEY) or "{}")
    except ValueError:
        return {}


def reviews(memory: Memory) -> dict[str, list[str]]:
    """Checkups screening -> why it is under review (a newer task force recommendation than Buddy's rules)."""
    out: dict[str, list[str]] = {}
    for w in _state(memory).get("watches") or []:
        if w.get("key") != "uspstf" or w.get("status") != "changed":
            continue
        for p in w.get("papers") or []:
            for item in p.get("items") or []:
                out.setdefault(item, []).append(f"The task force published “{p['title']}” ({p.get('date', '')}) after "
                                                f"Buddy's rules were checked on {_checked()}.")
    return out


def words(memory: Memory) -> dict[str, list[dict]]:
    """The task force's own words per Checkups screening, from its API (shown verbatim, as its terms require)."""
    try:
        by_alias = json.loads(memory.get(WORDS_KEY) or "{}")
    except ValueError:
        return {}
    out: dict[str, list[dict]] = {}
    for alias, texts in by_alias.items():
        for item in items_of(alias.replace("-", " ") + " " + " ".join(t["title"] for t in texts)):
            out.setdefault(item, []).extend(texts)
    return out


# --- Buddy itself: is a newer version waiting to be deployed?

def _git(*args: str, timeout: int = 20) -> str:
    out = subprocess.run(  # nosec B603 B607 - fixed git command, no shell, no user input
        ["git", "-c", "safe.directory=*", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if out.returncode != 0:
        raise ToolError((out.stderr or "git failed").strip().splitlines()[-1][:200])
    return out.stdout.strip()


def update(memory: Memory, git=None) -> dict:
    """Compare this server's code with the repository's branch (read only: nothing is fetched or changed)."""
    git = git or _git
    try:
        local, branch = git("rev-parse", "HEAD"), git("rev-parse", "--abbrev-ref", "HEAD")
        since = git("log", "-1", "--format=%cs")
        remote_line = git("ls-remote", "--heads", "origin", branch)
        remote = remote_line.split()[0] if remote_line else ""
        if not remote:
            state = {"status": "unknown", "detail": f"The repository has no branch {branch}."}
        elif remote == local:
            state = {"status": "current", "detail": f"Your server runs the latest version ({local[:7]}, {since})."}
        else:
            try:  # the repository's commit is already here and older: the server is ahead, not behind
                git("merge-base", "--is-ancestor", remote, local)
                state = {"status": "current", "detail": f"Your server runs {local[:7]} ({since})."}
            except ToolError:
                state = {"status": "changed", "detail": f"A newer version of Buddy is ready (your server runs "
                                                        f"{local[:7]}, from {since}).",
                         "command": "cd /opt/buddy && sudo git pull && sudo bash deploy/update.sh"}
    except (ToolError, OSError, subprocess.SubprocessError) as e:
        state = {"status": "unknown", "detail": f"Couldn't compare with the repository: {e}"}
    state["checked"] = local_now().isoformat(timespec="minutes")
    memory.set(UPDATE_KEY, json.dumps(state))
    return state


def update_due(memory: Memory) -> bool:
    try:
        last = json.loads(memory.get(UPDATE_KEY) or "{}").get("checked", "")
    except ValueError:
        last = ""
    return not last.startswith(local_now().date().isoformat())


def view(memory: Memory) -> dict:
    try:
        upd = json.loads(memory.get(UPDATE_KEY) or "{}")
    except ValueError:
        upd = {}
    state = _state(memory)
    return {"watches": state.get("watches") or [], "checked": state.get("checked", ""), "update": upd,
            "note": "Buddy's health rules are fixed in code so they stay exact; this only tells you when one may be "
                    "out of date. A flagged screening shows as under review on Labs → Checkups until the rules are "
                    "updated and deployed. Checked from official sources only, with nothing about you; no AI model "
                    "is involved."}


def check(memory, env=None, fetch=None) -> None:
    """Buddy Connect's test for the task force's API: the whole data set answers."""
    _uspstf_api(memory, env, fetch)

