"""Clean merchant names before they reach Money, the dashboard and the reviews.

Banks name the same business many ways: "SQ *SON CUBANO", "SON CUBANO RESTAURANT ON THE HUDSON",
"Son Cubano"; "AMZN Mktp US*2K4", "Amazon.com*RT5". Every transaction keeps the bank's own text in
transactions.merchant_raw; transactions.merchant is the clean name everything else groups by.

How a name is resolved, first match wins:
  1. Plaid's merchant_entity_id: the same business id is always the same merchant
  2. the user's own corrections (merge, rename), remembered as aliases
  3. known brands (Amazon, Uber vs Uber Eats, Apple, Google, Starbucks...)
  4. cleaning: payment-processor prefixes (SQ *, TST*, PAYPAL *), store numbers, phone numbers,
     web addresses, "INC/LLC", and the city/state the card network adds at the end
  5. a merchant already seen whose name starts the same (two or more words): "Son Cubano Restaurant
     On The Hudson" joins "Son Cubano"
Every raw name seen is remembered (merchant_aliases) with how it was resolved, so the result is stable
and the user's corrections win. reclean() applies the rules to every stored transaction again.
"""

import json
import re
from datetime import datetime, timezone

from .memory import Memory
from .tools import ToolError

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS merchants (
    key TEXT PRIMARY KEY,              -- "son cubano"
    name TEXT NOT NULL,                -- "Son Cubano"
    entity_id TEXT NOT NULL DEFAULT '',-- Plaid's merchant_entity_id, when known
    source TEXT NOT NULL,              -- plaid | brand | rule | user
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS merchant_aliases (
    raw TEXT PRIMARY KEY,              -- the bank's text, lower case, spaces squeezed
    key TEXT NOT NULL,                 -- -> merchants.key
    how TEXT NOT NULL,                 -- plaid | user | brand | rule | similar
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS merchants_entity ON merchants (entity_id);
"""

# Payment processors and wallets that prefix the real merchant.
PREFIX = re.compile(r"^(?:sq|tst|sp|pp|py|dd|bt|ck|in|pos|ach|chk|debit|purchase|recurring|checkcard|"
                    r"paypal|pypl|venmo|zelle|google|ggl|apple pay|apl\*? ?pay|square|toast|clover|stripe|"
                    r"bill ?pay|pmt|payment to)\s*[\*#:\-]\s*", re.I)
NOISE = re.compile(r"https?://\S+|www\.\S+|\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b|#\s*\d+|\*\s*[a-z0-9]{3,}\b|"
                   r"\b(?:store|str|ste|unit|no)\.?\s*\d+\b|\b\d{2,}\b|\b(?:inc|llc|ltd|corp|co|plc)\b\.?", re.I)
STATES = ("al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc "
          "nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy").split()
TAIL_STATE = re.compile(r"\s+(?:" + "|".join(STATES) + r")$", re.I)
# Card networks put the city before the state; these (plus the user's own area) are removed from the end.
CITIES = set("""new york|brooklyn|queens|bronx|staten island|manhattan|long island city|jersey city|hoboken|
west new york|union city|weehawken|north bergen|secaucus|newark|edison|princeton|paramus|hackensack|fort lee|
bayonne|harrison|kearny|morristown|parsippany|woodbridge|iselin|new brunswick|piscataway|philadelphia|boston|
chicago|los angeles|san francisco|san jose|seattle|austin|dallas|houston|miami|orlando|atlanta|washington|
arlington|denver|phoenix|las vegas|san diego|portland|charlotte|nashville|detroit|minneapolis|st louis|
baltimore|pittsburgh|cleveland|columbus|indianapolis|tampa|raleigh|salt lake city|sacramento|oakland|irvine|
santa clara|sunnyvale|mountain view|palo alto|redmond|bellevue|cambridge|stamford|white plains|yonkers|
hartford|providence|albany|buffalo""".replace("\n", "").split("|"))
FILLER = {"the", "on", "of", "at", "and", "&", "restaurant", "restaurants", "rest", "cafe", "bar", "grill",
          "kitchen", "store", "stores", "shop", "market", "co", "company", "llc", "inc", "us", "usa", "com"}
# Brands whose many spellings mean one merchant (pattern -> clean name). Order matters: specific first.
BRANDS = [
    (r"uber\s*\*?\s*eats|ubereats", "Uber Eats"), (r"\buber\b", "Uber"), (r"\blyft\b", "Lyft"),
    (r"amzn\s*mktp|amazon\s*mktpl|amazon\.com|amzn\.com|\bamzn\b|amazon\s*retail", "Amazon"),
    (r"prime\s*video|amazon\s*prime", "Amazon Prime"), (r"whole\s*foods|wholefds", "Whole Foods"),
    (r"apple\.com/bill|apple\s*services|itunes", "Apple"), (r"youtube\s*premium|google\s*\*?\s*youtube", "YouTube"),
    (r"google\s*\*?\s*(?:storage|one|cloud)", "Google One"), (r"netflix", "Netflix"), (r"spotify", "Spotify"),
    (r"hulu", "Hulu"), (r"disney\s*plus|disneyplus", "Disney+"), (r"starbucks|sbux", "Starbucks"),
    (r"doordash|dd\s*\*\s*door", "DoorDash"), (r"grubhub", "Grubhub"), (r"instacart", "Instacart"),
    (r"costco", "Costco"), (r"trader\s*joe", "Trader Joe's"), (r"target\b", "Target"), (r"walmart|wal-mart|wm\s+supercenter", "Walmart"),
    (r"shoprite", "ShopRite"), (r"cvs", "CVS"), (r"walgreens", "Walgreens"), (r"chipotle", "Chipotle"),
    (r"mcdonald", "McDonald's"), (r"dunkin", "Dunkin'"), (r"shell\s+(?:oil|service)", "Shell"),
    (r"exxon|exxonmobil", "Exxon"), (r"e-?z\s*pass", "E-ZPass"), (r"nj\s*transit", "NJ Transit"),
    (r"path\s*tapp|panynj", "PATH"), (r"mta\b|nyct", "MTA"), (r"etihad", "Etihad Airways"),
    (r"emirates", "Emirates"), (r"air\s*india", "Air India"), (r"united\s*air", "United Airlines"),
    (r"delta\s*air", "Delta"), (r"american\s*air", "American Airlines"), (r"jetblue", "JetBlue"),
    (r"marriott", "Marriott"), (r"hilton", "Hilton"), (r"hyatt", "Hyatt"), (r"airbnb", "Airbnb"),
    (r"lululemon", "lululemon"), (r"saks", "Saks Fifth Avenue"), (r"resy", "Resy"), (r"openai|chatgpt", "OpenAI"),
    (r"anthropic|claude\.ai", "Anthropic"), (r"digitalocean", "DigitalOcean"), (r"comcast|xfinity", "Xfinity"),
    (r"verizon", "Verizon"), (r"t-?mobile", "T-Mobile"), (r"at&t|att\s*\*?bill", "AT&T"), (r"pseg", "PSE&G"),
]
_BRANDS = [(re.compile(p, re.I), n) for p, n in BRANDS]
SMALL = {"and", "of", "the", "on", "at", "in", "for", "de", "la", "le", "a"}


def raw_key(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "").lower()).strip()[:120]


def clean_text(raw: str) -> str:
    """The bank's text without processor prefixes, numbers, web addresses, company suffixes and city/state."""
    s = str(raw or "").strip()
    for _ in range(2):  # "PAYPAL *SQ *SHOP"
        s = PREFIX.sub("", s)
    s = NOISE.sub(" ", s)
    s = re.sub(r"\.(com|net|org|co)\b", " ", s, flags=re.I)
    s = re.sub(r"[^\w&+' ]+|_", " ", s)  # letters in any language (Cebichería), digits, & + 
    s = re.sub(r"\s+", " ", s).strip()
    return _drop_place(s)


def _drop_place(s: str) -> str:
    """'SON CUBANO WEST NEW YORK NJ' -> 'SON CUBANO': the state code, then a known city before it."""
    words = s.split()
    if len(words) > 1 and TAIL_STATE.search(s):
        words = words[:-1]
    for n in (3, 2, 1):
        if len(words) > n and " ".join(words[-n:]).lower() in CITIES:
            words = words[:-n]
            break
    return " ".join(words) or s


def key_of(name: str) -> str:
    words = [w for w in re.sub(r"[^\w&+ ]|_", " ", name.lower()).split() if w not in FILLER]
    return " ".join(words[:5]) or name.lower().strip()[:40]


def pretty(name: str) -> str:
    """'SON CUBANO' -> 'Son Cubano'; keeps mixed-case names as they are ('lululemon', 'iCloud')."""
    if name != name.upper() and name != name.lower():
        return name.strip()
    words = name.lower().split()
    return " ".join(w if (i and w in SMALL) else (w.upper() if len(w) <= 3 and not re.search(r"[aeiou]", w)
                                                    else w.capitalize()) for i, w in enumerate(words))


def brand(raw: str) -> str | None:
    for pattern, name in _BRANDS:
        if pattern.search(raw or ""):
            return name
    return None


class Merchants:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    def _upsert(self, key: str, name: str, source: str, entity_id: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        row = self.memory.db.execute("SELECT * FROM merchants WHERE key = ?", (key,)).fetchone()
        if row is None:
            self.memory.db.execute("INSERT INTO merchants (key, name, entity_id, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                                   (key, name[:80], entity_id, source, now))
        elif entity_id and not row["entity_id"]:
            self.memory.db.execute("UPDATE merchants SET entity_id = ? WHERE key = ?", (entity_id, key))

    def _alias(self, raw: str, key: str, how: str) -> None:
        self.memory.db.execute("INSERT OR REPLACE INTO merchant_aliases (raw, key, how, created_at) VALUES (?, ?, ?, ?)",
                               (raw_key(raw), key, how, datetime.now(timezone.utc).isoformat()))

    def _name(self, key: str) -> str:
        row = self.memory.db.execute("SELECT name FROM merchants WHERE key = ?", (key,)).fetchone()
        return row["name"] if row else key

    def _similar(self, key: str) -> str | None:
        """A merchant already seen whose clean name starts the same, two words or more ('son cubano')."""
        words = key.split()
        if len(words) < 2:
            return None
        best = None
        for row in self.memory.db.execute("SELECT key FROM merchants WHERE source != 'brand'"):
            other = row["key"].split()
            n = min(len(words), len(other))
            if n >= 2 and words[:n] == other[:n] and (best is None or len(other) < len(best.split())):
                best = row["key"]
        return best

    def resolve(self, raw: str, entity_id: str = "", plaid_name: str = "") -> str:
        """The clean name for a bank's merchant text (and Plaid's own name and id when there are)."""
        raw = (raw or plaid_name or "").strip()
        if not raw:
            return "unknown"
        hit = self.memory.db.execute(
            "SELECT a.key, a.how FROM merchant_aliases a WHERE a.raw = ?", (raw_key(raw),)).fetchone()
        if hit and hit["how"] == "user":
            return self._name(hit["key"])
        if entity_id:
            row = self.memory.db.execute("SELECT key FROM merchants WHERE entity_id = ?", (entity_id,)).fetchone()
            if row:
                self._alias(raw, row["key"], "plaid")
                return self._name(row["key"])
        if hit:
            if entity_id:
                self._upsert(hit["key"], self._name(hit["key"]), "plaid", entity_id)
            return self._name(hit["key"])
        name, how = brand(raw) or brand(plaid_name), "brand"
        if not name:
            text = clean_text(plaid_name) if plaid_name else clean_text(raw)
            name, how = pretty(text or raw), "rule"
        key = key_of(name)
        if how == "rule" and not self.memory.db.execute("SELECT 1 FROM merchants WHERE key = ?", (key,)).fetchone():
            similar = self._similar(key)
            if similar:  # the group keeps the shortest clean name: "Son Cubano", not "...Restaurant On The Hudson"
                row = self.memory.db.execute("SELECT name, source FROM merchants WHERE key = ?", (similar,)).fetchone()
                if row["source"] == "rule" and len(name.split()) < len(row["name"].split()):
                    self.memory.db.execute("UPDATE merchants SET name = ? WHERE key = ?", (name[:80], similar))
                    try:  # charges already stored under the longer name follow
                        self.memory.db.execute("UPDATE transactions SET merchant = ? WHERE merchant = ?",
                                               (name[:80], row["name"]))
                    except Exception:
                        pass
                key, how = similar, "similar"
        self._upsert(key, name, "plaid" if entity_id else ("brand" if how == "brand" else "rule"), entity_id)
        self._alias(raw, key, how)
        return self._name(key)

    # --- the user's corrections ---------------------------------------------------------------
    def merge(self, names: list[str], into: str) -> str:
        """These names are all one place, called `into`."""
        into = (into or "").strip()
        if not into or not names:
            raise ToolError("Which names are the same place, and what should it be called?")
        key = key_of(into)
        self._upsert(key, into, "user")
        self.memory.db.execute("UPDATE merchants SET name = ?, source = 'user' WHERE key = ?", (into[:80], key))
        rows = self.memory.db.execute("SELECT DISTINCT merchant, merchant_raw FROM transactions").fetchall()
        moved = 0
        wanted = [n.strip().lower() for n in names + [into] if n and n.strip()]
        for r in rows:
            for text in (r["merchant"], r["merchant_raw"]):
                if text and any(w == text.lower() or w in text.lower() for w in wanted):
                    self._alias(r["merchant_raw"] or r["merchant"], key, "user")
                    old_key = key_of(r["merchant"])
                    if old_key != key:
                        self.memory.db.execute("UPDATE merchant_aliases SET key = ?, how = 'user' WHERE key = ?",
                                               (key, old_key))
                    moved += 1
                    break
        for n in names:
            self._alias(n, key, "user")
        self.memory.db.commit()
        changed = self.reclean()
        return f"From now on these are all {into}: {changed} transaction{'s' if changed != 1 else ''} updated."

    def rename(self, name: str, to: str) -> str:
        return self.merge([name], to)

    def top(self, limit: int = 40) -> list[dict]:
        """Merchants by transactions, each with the bank's spellings that were grouped into it."""
        out = []
        for r in self.memory.db.execute(
                "SELECT merchant, COUNT(*) AS n, ROUND(SUM(amount), 2) AS total FROM transactions "
                "GROUP BY merchant ORDER BY n DESC LIMIT ?", (limit,)):
            raws = [x["merchant_raw"] for x in self.memory.db.execute(
                "SELECT DISTINCT merchant_raw FROM transactions WHERE merchant = ? AND merchant_raw != '' LIMIT 6",
                (r["merchant"],))]
            out.append({"merchant": r["merchant"], "transactions": r["n"], "total": r["total"], "bank_spellings": raws})
        return out

    def reclean(self) -> int:
        """Apply the current rules and corrections to every stored transaction. Returns how many changed."""
        changed = 0
        rows = self.memory.db.execute("SELECT id, merchant, merchant_raw FROM transactions").fetchall()
        for r in rows:
            raw = r["merchant_raw"] or r["merchant"]
            clean = self.resolve(raw)
            if not r["merchant_raw"] or clean != r["merchant"]:
                self.memory.db.execute("UPDATE transactions SET merchant = ?, merchant_raw = ? WHERE id = ?",
                                       (clean[:80], raw[:120], r["id"]))
                changed += clean != r["merchant"]
        self.memory.db.commit()
        return changed


MERCHANTS_TOOL = {
    "name": "merchants",
    "description": "Clean shop names in Money and the dashboard: banks spell one place many ways (\"SQ *SON CUBANO\", "
    "\"SON CUBANO RESTAURANT ON THE HUDSON\"), and Buddy groups them. list: the top merchants with the bank "
    "spellings grouped into each. merge (names, into): \"Son Cubano and Son Cubano Restaurant are the same\" -> "
    "names [both], into \"Son Cubano\"; also for a wrong grouping fixed by naming it. rename (name, to). "
    "Corrections are remembered and applied to past and future transactions.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "merge", "rename"]},
        "names": {"type": "array", "items": {"type": "string"}}, "into": {"type": "string"},
        "name": {"type": "string"}, "to": {"type": "string"}},
        "required": ["action"], "additionalProperties": False},
}


def run_tool(memory: Memory, args: dict) -> str:
    m = Merchants(memory)
    a = args.get("action")
    if a == "merge":
        return m.merge([str(n) for n in args.get("names") or []], str(args.get("into") or ""))
    if a == "rename":
        return m.rename(str(args.get("name") or ""), str(args.get("to") or ""))
    return json.dumps(m.top(), ensure_ascii=False)
