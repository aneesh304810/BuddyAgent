"""Fund holdings: what well-known investors' funds reported owning, from their quarterly 13F filings at the SEC.

  Weekly: for each fund followed, the SEC's list of its filings -> a 13F not seen before -> its holdings table
  (issuer, class, value, shares, puts and calls) kept in the price book -> what changed against the quarter
  before: new positions, positions sold out, added to, trimmed, and how concentrated the fund is.

What a 13F is and isn't: US-listed long positions (and listed options) at the END of a quarter, filed up to 45
days later. No short positions, no cash, no foreign listings, nothing bought or sold since. So every answer
carries the quarter and the filing date, and is never "what they own now". A filing lists issuer names and CUSIP
numbers, not tickers, so companies are matched by name.

The SEC numbers (CIK) below were written from memory and could not be checked when this was built: each fund's
entry shows the name the SEC returns for it, so a wrong number is visible, and "follow <name> <CIK>" corrects
or adds one. Only the CIK goes to the SEC.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger(__name__)

FUNDS_KEY, WEEK_KEY, PENDING_KEY = "funds:followed", "funds:week", "funds:pending"
DEFAULT = {
    "Berkshire Hathaway": "1067983", "Bridgewater Associates": "1350694", "Pershing Square": "1336528",
    "Scion Asset Management": "1649339", "Renaissance Technologies": "1037389", "Appaloosa": "1656456",
    "Greenlight Capital": "1079114",
}
NOTE = ("From 13F filings: US-listed long positions at the quarter's end, filed up to 45 days later. Not what the "
        "fund owns now; no shorts, cash or foreign holdings.")


def _ready(memory: Memory) -> None:
    from . import pricebook  # noqa: PLC0415
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS fund_filings (accession VARCHAR PRIMARY KEY, fund VARCHAR, "
                          "cik VARCHAR, sec_name VARCHAR, quarter DATE, filed DATE, total DOUBLE, positions INTEGER)")
    pricebook.run(memory, "CREATE TABLE IF NOT EXISTS fund_holdings (accession VARCHAR, issuer VARCHAR, "
                          "cusip VARCHAR, cls VARCHAR, putcall VARCHAR, value DOUBLE, shares DOUBLE)")


def followed(memory: Memory) -> dict:
    saved = json.loads(memory.get(FUNDS_KEY) or "{}")
    out = dict(DEFAULT)
    for name, cik in saved.items():
        if cik:
            out[name] = cik
        else:
            out.pop(name, None)
    return out


def follow(memory: Memory, name: str, cik: str) -> str:
    name, cik = " ".join((name or "").split())[:60], re.sub(r"\D", "", str(cik or "")).lstrip("0")
    if not name or not cik:
        raise ToolError("Give the fund's name and its SEC number (CIK), from sec.gov's company search.")
    saved = json.loads(memory.get(FUNDS_KEY) or "{}")
    saved[name] = cik
    memory.set(FUNDS_KEY, json.dumps(saved))
    return f"Following {name} (SEC number {cik}). Say 'refresh the fund holdings' to read its latest filing."


def unfollow(memory: Memory, name: str) -> str:
    hit = _which(memory, name)
    saved = json.loads(memory.get(FUNDS_KEY) or "{}")
    saved[hit] = ""
    memory.set(FUNDS_KEY, json.dumps(saved))
    return f"Stopped following {hit}."


def _which(memory: Memory, name: str) -> str:
    words = (name or "").lower().split()
    for f in followed(memory):
        if words and all(w in f.lower() for w in words):
            return f
    raise ToolError(f"Which fund? Following: {', '.join(followed(memory))}.")


# --- reading the SEC ------------------------------------------------------------------------------------------
def _headers(env=None) -> dict:
    from .market_school import _sec_headers  # noqa: PLC0415
    return _sec_headers(env)


def latest_13f(memory: Memory, cik: str, env=None, fetch=None) -> dict | None:
    """The fund's newest 13F-HR: {accession, quarter, filed, sec_name}; None when it has none."""
    got = gateway.call(memory, "sec", f"/submissions/CIK{cik.zfill(10)}.json", headers=_headers(env), env=env,
                       fetch=fetch, purpose="funds") or {}
    recent = (got.get("filings") or {}).get("recent") or {}
    for form, acc, filed, period in zip(recent.get("form", []), recent.get("accessionNumber", []),
                                        recent.get("filingDate", []), recent.get("reportDate", [])):
        if form in ("13F-HR", "13F-HR/A") and period:
            return {"accession": acc, "quarter": period, "filed": filed, "sec_name": got.get("name", "")}
    return None


def holdings(memory: Memory, cik: str, accession: str, filed: str, env=None, fetch=None) -> list[dict]:
    """The filing's holdings table, one row per issuer and class (lots added together)."""
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    index = gateway.call_url(memory, "sec", [f"{folder}/index.json"], headers=_headers(env), fetch=fetch,
                             purpose="funds") or {}
    names = [str(i.get("name", "")) for i in (index.get("directory") or {}).get("item") or []]
    tables = [n for n in names if n.lower().endswith(".xml") and "primary_doc" not in n.lower()]
    if not tables:
        raise ToolError("The filing has no holdings table.")
    raw = gateway.call_url(memory, "sec", [f"{folder}/{tables[0]}"], headers={**_headers(env), "Accept": "*/*"},
                           raw=True, fetch=fetch, purpose="funds")
    root = ET.fromstring(raw)
    scale = 1.0 if filed >= "2023-01-03" else 1000.0  # values were in thousands of dollars before 2023

    def text(el, name):
        for child in el.iter():
            if child.tag.rsplit("}", 1)[-1] == name:
                return (child.text or "").strip()
        return ""

    rows: dict[tuple, dict] = {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "infoTable":
            continue
        try:
            value, shares = float(text(el, "value") or 0) * scale, float(text(el, "sshPrnamt") or 0)
        except ValueError:
            continue
        key = (text(el, "nameOfIssuer").upper(), text(el, "cusip"), text(el, "putCall"))
        r = rows.setdefault(key, {"issuer": key[0], "cusip": key[1], "cls": text(el, "titleOfClass"),
                                  "putcall": key[2], "value": 0.0, "shares": 0.0})
        r["value"] += value
        r["shares"] += shares
    return sorted(rows.values(), key=lambda r: -r["value"])


def refresh(memory: Memory, env=None, fetch=None) -> dict:
    """Read every followed fund's newest 13F if it's new. {fund: status}; one failing never stops the rest."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    out = {}
    for fund, cik in followed(memory).items():
        try:
            f = latest_13f(memory, cik, env, fetch)
            if not f:
                out[fund] = "no 13F filings under that SEC number"
                continue
            if pricebook.run(memory, "SELECT 1 FROM fund_filings WHERE accession = ?", (f["accession"],)):
                out[fund] = f"up to date (quarter to {f['quarter']})"
                continue
            rows = holdings(memory, cik, f["accession"], f["filed"], env, fetch)
            pricebook.run(memory, "INSERT INTO fund_filings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (f["accession"], fund, cik, f["sec_name"], f["quarter"], f["filed"],
                           sum(r["value"] for r in rows), len(rows)))
            for r in rows:
                pricebook.run(memory, "INSERT INTO fund_holdings VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (f["accession"], r["issuer"], r["cusip"], r["cls"], r["putcall"], r["value"], r["shares"]))
            out[fund] = f"new filing: quarter to {f['quarter']}, filed {f['filed']}, {len(rows)} positions"
        except Exception as e:
            log.info("funds %s: %s", fund, e)
            out[fund] = f"failed: {str(e)[:100]}"
    return out


# --- reading it back ------------------------------------------------------------------------------------------
def _filings(memory: Memory, fund: str, n: int = 2) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    return pricebook.run(memory, "SELECT * FROM fund_filings WHERE fund = ? ORDER BY quarter DESC, filed DESC "
                                 "LIMIT ?", (fund, n))


def _rows(memory: Memory, accession: str) -> dict:
    from . import pricebook  # noqa: PLC0415
    return {(r["issuer"], r["putcall"]): r for r in pricebook.run(
        memory, "SELECT issuer, putcall, cls, value, shares FROM fund_holdings WHERE accession = ?", (accession,))}


def _label(key: tuple) -> str:
    return key[0].title() + (f" ({key[1].lower()} options)" if key[1] else "")


def show(memory: Memory, fund: str, top: int = 15) -> dict:
    """A fund's latest reported holdings, largest first, with what changed since the quarter before."""
    fund = _which(memory, fund)
    files = _filings(memory, fund)
    if not files:
        return {"fund": fund, "note": "Not read yet: say 'refresh the fund holdings'."}
    now, f = _rows(memory, files[0]["accession"]), files[0]
    total = f["total"] or 1
    out = {"fund": fund, "sec_name": f["sec_name"], "quarter_ending": str(f["quarter"]), "filed": str(f["filed"]),
           "reported_value": round(total), "positions": f["positions"],
           "top": [{"holding": _label(k), "value": round(r["value"]), "share_of_fund": round(r["value"] / total, 3)}
                   for k, r in sorted(now.items(), key=lambda kv: -kv[1]["value"])[:top]],
           "top_10_share": round(sum(sorted((r["value"] for r in now.values()), reverse=True)[:10]) / total, 3),
           "note": NOTE}
    if len(files) > 1:
        before = _rows(memory, files[1]["accession"])
        moved = lambda k: now[k]["shares"] / before[k]["shares"] - 1 if before[k]["shares"] else 0  # noqa: E731
        both = [k for k in now if k in before]
        by_value = lambda ks: sorted(ks, key=lambda k: -(now.get(k) or before[k])["value"])  # noqa: E731
        out["changes_since"] = str(files[1]["quarter"])
        out["new"] = [_label(k) for k in by_value([k for k in now if k not in before])][:12]
        out["sold_out"] = [_label(k) for k in by_value([k for k in before if k not in now])][:12]
        out["added"] = [f"{_label(k)} ({moved(k):+.0%} shares)" for k in by_value(both) if moved(k) >= 0.1][:12]
        out["trimmed"] = [f"{_label(k)} ({moved(k):+.0%} shares)" for k in by_value(both) if moved(k) <= -0.1][:12]
    return out


def who_holds(memory: Memory, company: str) -> dict:
    """Which followed funds reported holding a company (matched by name) in their latest filing."""
    from . import pricebook  # noqa: PLC0415
    _ready(memory)
    words = [w for w in re.findall(r"[a-z0-9]+", (company or "").lower())
             if w not in ("inc", "corp", "corporation", "co", "company", "the", "plc", "ltd", "class", "com")][:3]
    if not words:
        raise ToolError("Which company? Give its name (filings list names, not tickers).")
    like = " AND ".join("lower(h.issuer) LIKE ?" for _ in words)
    out = []
    for fund in followed(memory):
        files = _filings(memory, fund, 1)
        if not files:
            continue
        rows = pricebook.run(memory, f"SELECT h.issuer, h.putcall, h.value, h.shares FROM fund_holdings h WHERE "
                                     f"h.accession = ? AND {like} ORDER BY h.value DESC",
                             (files[0]["accession"], *[f"%{w}%" for w in words]))
        for r in rows[:3]:
            out.append({"fund": fund, "holding": _label((r["issuer"], r["putcall"])), "value": round(r["value"]),
                        "share_of_fund": round(r["value"] / (files[0]["total"] or 1), 3),
                        "quarter_ending": str(files[0]["quarter"]), "filed": str(files[0]["filed"])})
    return {"company": company, "held_by": out, "note": NOTE + " Matched by company name, so check the name."}


def status(memory: Memory) -> dict:
    out = {}
    for fund, cik in followed(memory).items():
        f = _filings(memory, fund, 1)
        out[fund] = {"sec_number": cik, **({"sec_name": f[0]["sec_name"], "quarter_ending": str(f[0]["quarter"]),
                                            "filed": str(f[0]["filed"]), "positions": f[0]["positions"]}
                                           if f else {"read": "not yet"})}
    return {"funds": out, "note": NOTE + " If a fund's sec_name isn't the fund you meant, its SEC number is wrong: "
                                         "say 'follow <name> <number>' with the right one."}


# --- weekly ---------------------------------------------------------------------------------------------------
def due(memory: Memory, now: datetime | None = None) -> bool:
    now = now or local_now()
    year, week, _ = now.isocalendar()
    return now.hour >= 9 and memory.get(WEEK_KEY) != f"{year}-{week}"


FRESH_DAYS = 60  # a filing older than this isn't announced


def weekly(memory: Memory, env=None, fetch=None, today=None) -> str:
    """Read new filings; a short note when a fund filed, '' otherwise."""
    got = refresh(memory, env, fetch)
    today, blocks, names = (today or local_now().date()), [], []
    for fund, text in got.items():
        if not text.startswith("new filing"):
            continue
        s = show(memory, fund, 3)
        if (today - date.fromisoformat(s["filed"])).days > FRESH_DAYS:
            continue  # read for the record, but an old filing (or a fund's first read) is not news
        names.append(fund)
        blocks.append(f"**{fund}** · quarter to {s['quarter_ending']}, filed {s['filed']}")
        blocks.append(f"- Largest: {', '.join(t['holding'] for t in s['top'])}")
        blocks += [f"- {label}: {', '.join(s[key][:4])}" for key, label in
                   (("new", "New"), ("sold_out", "Sold out of"), ("added", "Added to"), ("trimmed", "Trimmed"))
                   if s.get(key)]
    if not blocks:
        return ""
    who = names[0] if len(names) == 1 else f"{len(names)} funds"
    return (f"🏦 New quarterly filing from {who}. The details are on screen. || **🏦 New fund filings**\n"
            + "\n".join(blocks) + "\nQuarter-end positions, already weeks old: not what they own now.")



TOOL = {
    "name": "fund_holdings",
    "description": (
        "What well-known funds reported owning in their quarterly SEC 13F filings (Berkshire Hathaway, Bridgewater, "
        "Pershing Square, Scion, Renaissance, Appaloosa, Greenlight, and any the owner adds). show (fund): its "
        "largest holdings with each one's share of the fund, how concentrated it is, and what was new, sold out, "
        "added to or trimmed since the quarter before. who_holds (company: its NAME): which followed funds "
        "reported holding it. status: the funds followed and each one's latest filing. refresh: read new filings "
        "now (done weekly anyway). follow (fund, cik) / unfollow (fund). Always give the quarter end and filing "
        "date, and say a 13F is weeks old, long US positions only, and never what the fund owns today."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["show", "who_holds", "status", "refresh", "follow", "unfollow"]},
        "fund": {"type": "string"}, "company": {"type": "string"}, "cik": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    a = args.get("action")
    if a == "show":
        return json.dumps(show(memory, args.get("fund", "")))
    if a == "who_holds":
        return json.dumps(who_holds(memory, args.get("company", "")))
    if a == "refresh":
        return json.dumps(refresh(memory))
    if a == "follow":
        return follow(memory, args.get("fund", ""), args.get("cik", ""))
    if a == "unfollow":
        return unfollow(memory, args.get("fund", ""))
    return json.dumps(status(memory))
