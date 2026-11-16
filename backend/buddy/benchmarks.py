"""Benchmarks and symbol lists for the strategy lab: academic factor returns, and fund holdings as stock lists.

  Factor returns   the Kenneth French data library publishes, as files meant for download, what the US market
                   and the classic styles returned each month since 1926: the market above cash, small against
                   large companies, cheap against expensive ones, and past winners against past losers. Buddy
                   fetches the two files once a month and keeps the monthly numbers. They are a yardstick, not
                   something to trade: a rule that looks good should be compared with what plain momentum or
                   the market itself returned over the same time.
  Symbol lists     what a fund holds is a list of stocks with a date. Buddy can fetch a fund's published holdings
                   file (off until you switch it on: its provider's terms were not checked from here), or you
                   upload one. Each list is kept with its date and never overwritten, so past membership builds
                   up from the day you start. One list can be chosen as the lab's stock universe.

Files are opened in the sandbox (docread.py). Only file names and fund names go out; nothing about you.
"""

import json
import logging
import re
from datetime import date

from . import gateway
from .memory import Memory
from .tools import ToolError, local_now

log = logging.getLogger("buddy.benchmarks")

FRENCH = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FACTOR_FILES = {"F-F_Research_Data_Factors_CSV.zip": ("market", "size", "value", "cash"),
                "F-F_Momentum_Factor_CSV.zip": ("momentum",)}
FACTOR_WORDS = {"market": "US stocks above cash", "size": "small companies against large",
                "value": "cheap companies against expensive", "momentum": "past winners against past losers"}
HOLDINGS = {  # name -> (title, the provider's published holdings file)
    "sp500": ("S&P 500 (from the SPY fund's holdings)",
              "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"),
    "dow": ("Dow 30 (from the DIA fund's holdings)",
            "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-dia.xlsx"),
}
MONTH_KEY, UNIVERSE_KEY, HOLDINGS_ON_KEY = "benchmarks:month", "stratlab:universe", "benchmarks:holdings_on"
TICKER = re.compile(r"[A-Z]{1,5}(?:[.\-][A-Z])?")
SCHEMA = ["CREATE TABLE IF NOT EXISTS factor_returns (month VARCHAR, name VARCHAR, value DOUBLE, PRIMARY KEY (month, name))",
          "CREATE TABLE IF NOT EXISTS symbol_lists (name VARCHAR, day DATE, title VARCHAR, source VARCHAR, symbols VARCHAR, "
          "PRIMARY KEY (name, day))"]


def _run(memory: Memory, sql: str, params: tuple = ()) -> list[dict]:
    from . import pricebook  # noqa: PLC0415
    return pricebook.run(memory, sql, params)


def _ready(memory: Memory) -> None:
    for statement in SCHEMA:
        _run(memory, statement)


# --- factor returns ---------------------------------------------------------------------------------------------
def parse_factors(text: str, names: tuple) -> list[tuple[str, str, float]]:
    """The monthly rows of a French file: (YYYY-MM, name, fraction). The yearly rows further down are left out."""
    out = []
    for line in text.splitlines():
        m = re.fullmatch(r"\s*(\d{4})(\d{2})\s*,(.+)", line)
        if not m or not 1 <= int(m.group(2)) <= 12:
            continue
        cells = [c.strip() for c in m.group(3).split(",")]
        for name, cell in zip(names, cells):
            try:
                value = float(cell)
            except ValueError:
                continue
            if value > -99:  # -99.99 marks a missing month
                out.append((f"{m.group(1)}-{m.group(2)}", name, round(value / 100, 6)))
    return out


async def refresh_factors(memory: Memory, fetch=None, reader=None) -> int:
    """Fetch the two files and keep the monthly numbers. Returns how many month-values are held."""
    import asyncio  # noqa: PLC0415
    from . import docread  # noqa: PLC0415
    _ready(memory)
    for name, cols in FACTOR_FILES.items():
        raw = await asyncio.to_thread(lambda n=name: gateway.call_url(memory, "french_data", [FRENCH + n], raw=True,
                                                                      fetch=fetch, purpose="benchmarks"))
        got = await (reader or docread.read)("factors.zip", raw if isinstance(raw, bytes) else str(raw).encode())
        rows = parse_factors(got["text"], cols)
        if not rows:
            raise ToolError(f"The factor file {name} could not be read" + (f" ({got['error']})" if got["error"] else "."))
        for month, col, value in rows:
            _run(memory, "INSERT OR REPLACE INTO factor_returns (month, name, value) VALUES (?, ?, ?)", (month, col, value))
    memory.set(MONTH_KEY, local_now().date().isoformat()[:7])
    return _run(memory, "SELECT COUNT(*) AS n FROM factor_returns")[0]["n"]


def factors(memory: Memory) -> dict:
    """What each style returned: the last 12 months and the long-run yearly average, with the months covered."""
    _ready(memory)
    rows = _run(memory, "SELECT month, name, value FROM factor_returns WHERE name != 'cash' ORDER BY month")
    if not rows:
        return {"rows": [], "note": "Not fetched yet."}
    out = []
    for name, words in FACTOR_WORDS.items():
        mine = [r["value"] for r in rows if r["name"] == name]
        if not mine:
            continue
        year = 1.0
        for v in mine[-12:]:
            year *= 1 + v
        out.append({"name": name, "what": words, "last_12_months": round(year - 1, 4),
                    "average_year": round(sum(mine) / len(mine) * 12, 4), "months": len(mine)})
    return {"rows": out, "from": rows[0]["month"], "to": rows[-1]["month"],
            "note": "From the Kenneth French data library. A yardstick for a rule's results, not a signal to trade."}


def factors_due(memory: Memory, now=None) -> bool:
    now = now or local_now()
    return now.day >= 3 and (memory.get(MONTH_KEY) or "") != now.date().isoformat()[:7]


# --- symbol lists -----------------------------------------------------------------------------------------------
def symbols_in(text: str) -> list[str]:
    """The tickers in a holdings table: the column headed Ticker (or Symbol), cash and blank rows left out."""
    from .docread import rows as table  # noqa: PLC0415
    grid = table(text)
    col = start = None
    for i, row in enumerate(grid[:40]):
        for j, cell in enumerate(row):
            if cell.strip().lower() in ("ticker", "symbol", "ticker symbol", "holding ticker"):
                col, start = j, i + 1
                break
        if col is not None:
            break
    if col is None:
        raise ToolError("No column headed Ticker or Symbol was found in that file.")
    out = []
    for row in grid[start:]:
        cell = row[col].strip().upper() if col < len(row) else ""
        if TICKER.fullmatch(cell) and cell not in ("USD", "CASH", "N/A"):
            out.append(cell.replace(".", "-"))
    return list(dict.fromkeys(out))


def keep_list(memory: Memory, name: str, title: str, symbols: list[str], source: str, day: date | None = None) -> dict:
    _ready(memory)
    name = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:30]
    if not name or len(symbols) < 5:
        raise ToolError("A list needs a name and at least five symbols.")
    day = day or local_now().date()
    _run(memory, "INSERT OR REPLACE INTO symbol_lists (name, day, title, source, symbols) VALUES (?, ?, ?, ?, ?)",
         (name, day.isoformat(), str(title)[:120], str(source)[:200], json.dumps(symbols[:1500])))
    return {"name": name, "day": day.isoformat(), "symbols": len(symbols)}


async def fetch_holdings(memory: Memory, name: str, fetch=None, reader=None) -> dict:
    import asyncio  # noqa: PLC0415
    from . import docread  # noqa: PLC0415
    if name not in HOLDINGS:
        raise ToolError("Unknown list.")
    title, url = HOLDINGS[name]
    raw = await asyncio.to_thread(lambda: gateway.call_url(memory, "fund_holdings", [url], raw=True, fetch=fetch,
                                                           purpose="benchmarks"))
    got = await (reader or docread.read)("holdings.xlsx", raw if isinstance(raw, bytes) else str(raw).encode())
    return keep_list(memory, name, title, symbols_in(got["text"]), url)


async def upload_list(memory: Memory, name: str, filename: str, data: bytes, reader=None) -> dict:
    from . import docread  # noqa: PLC0415
    got = await (reader or docread.read)(filename, data)
    if got["error"] and not got["text"]:
        raise ToolError(f"Buddy could not read that file ({got['error']}).")
    return keep_list(memory, name, name, symbols_in(got["text"]), "uploaded: " + filename[:80])


def lists(memory: Memory) -> list[dict]:
    """Each list's latest version, with how many dated versions are kept and whether the lab uses it."""
    _ready(memory)
    rows = _run(memory, "SELECT name, MAX(day) AS day, COUNT(*) AS versions FROM symbol_lists GROUP BY name ORDER BY name")
    used, out = memory.get(UNIVERSE_KEY) or "", []
    for r in rows:
        latest = _run(memory, "SELECT title, source, symbols FROM symbol_lists WHERE name = ? AND day = ?", (r["name"], r["day"]))[0]
        out.append({"name": r["name"], "title": latest["title"], "day": str(r["day"]), "versions": r["versions"],
                    "symbols": len(json.loads(latest["symbols"])), "source": latest["source"], "in_use": r["name"] == used})
    return out


def lab_symbols(memory: Memory) -> list[str]:
    """The symbols of the list chosen as the lab's universe (its latest version); [] when none is chosen."""
    name = memory.get(UNIVERSE_KEY) or ""
    if not name:
        return []
    try:
        _ready(memory)
        rows = _run(memory, "SELECT symbols FROM symbol_lists WHERE name = ? ORDER BY day DESC LIMIT 1", (name,))
        return json.loads(rows[0]["symbols"])[:600] if rows else []
    except Exception:
        log.info("benchmarks: the lab's list is unavailable", exc_info=True)
        return []


def use_in_lab(memory: Memory, name: str) -> str:
    """Choose a list as the lab's universe ("" goes back to the built-in list)."""
    if name and not any(l["name"] == name for l in lists(memory)):
        raise ToolError("That list isn't there.")
    memory.set(UNIVERSE_KEY, name)
    return (f"The strategy lab now trades the {name} list. Replay over past prices to see each rule on it."
            if name else "The strategy lab is back on its built-in list.")


def view(memory: Memory) -> dict:
    return {"factors": factors(memory), "lists": lists(memory),
            "presets": [{"name": k, "title": v[0]} for k, v in HOLDINGS.items()],
            "holdings_on": memory.get(HOLDINGS_ON_KEY) == "1"}
