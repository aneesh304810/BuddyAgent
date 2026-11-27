"""Product identity: which product a deal headline is about, its brand and its category.

A price is only history if the same product is filed under the same name each time. Deal headlines are not that:
"Amazon - Baseus S2 4K Solar Wireless Outdoor Security Camera $79.99" carries the store and today's price, so
every headline is a new "product" and nothing ever has a history. This module reads a headline into

    product   the brand and the model ("baseus s2"); a multi-pack is its own product ("wyze solar cam pan x2")
    brand     "baseus"
    category  "security camera"

so prices are collected per product, and can also be compared across a brand or a category when a product is new.

Travel is filed the same way, with its own words:

    flight   product = the route ("mia-lim"), place = the destination ("lim"), brand = the airline,
             category = the cabin (economy, premium economy, business, first; "not stated" when nobody said)
    hotel    product = the hotel or the place searched, place = the place, brand = the chain, category = "hotel"

A business fare is never compared with an economy one: the cabin is part of what a flight price is a price of.
Rules only, no AI model: the same headline always gives the same answer. It is a best reading, not a catalogue.
When no brand or model can be read, the cleaned headline is the product, which is still stable.
"""

import re

BRANDS = (  # as written in headlines; two-word brands first so they match whole
    "under armour", "new balance", "north face", "black decker", "black+decker", "western digital", "tp-link",
    "hamilton beach", "le creuset", "kitchenaid", "instant pot", "fisher-price", "hot wheels", "t-fal",
    "apple", "samsung", "sony", "lg", "bose", "jbl", "anker", "soundcore", "eufy", "baseus", "ugreen", "belkin", "roku",
    "amazon", "kindle", "echo", "ring", "blink", "wyze", "arlo", "reolink", "soliom", "tapo", "google", "nest", "pixel",
    "microsoft", "xbox", "surface", "nintendo", "playstation", "meta", "dell", "alienware", "hp", "lenovo", "asus", "acer",
    "msi", "razer", "logitech", "corsair", "steelseries", "hyperx", "sandisk", "crucial", "kingston", "seagate", "wd",
    "netgear", "linksys", "garmin", "fitbit", "oura", "gopro", "dji", "canon", "nikon", "fujifilm", "epson", "brother",
    "tcl", "hisense", "vizio", "philips", "sonos", "sennheiser", "beats", "skullcandy", "shokz", "jabra", "nothing",
    "oneplus", "motorola", "dyson", "shark", "bissell", "irobot", "roomba", "roborock", "ecovacs", "eureka", "ninja",
    "cuisinart", "keurig", "nespresso", "breville", "lavazza", "vitamix", "oxo", "lodge", "pyrex", "rubbermaid", "yeti",
    "stanley", "hydro flask", "contigo", "dewalt", "milwaukee", "makita", "ryobi", "bosch", "craftsman", "kobalt",
    "greenworks", "ego", "husky", "ridgid", "weber", "traeger", "blackstone", "coleman", "nike", "adidas", "puma",
    "reebok", "skechers", "asics", "brooks", "hoka", "crocs", "levi's", "levis", "carhartt", "columbia", "patagonia",
    "uniqlo", "lego", "barbie", "nerf", "hasbro", "mattel", "pampers", "huggies", "tide", "bounty", "charmin", "crest",
    "oral-b", "gillette", "cerave", "neutrogena", "energizer", "duracell", "govee", "kasa", "ecoflow", "jackery",
    "bluetti", "segway", "peloton", "bowflex", "samsonite", "osprey", "casper", "purple", "tempur", "zinus", "ikea",
)
CATEGORIES = (  # the first that matches wins, so the specific ones come before the general
    ("video doorbell", r"doorbell"),
    ("security camera", r"security cam|\bcam(?:era)? kit\b|solar cam|homebase|\beufycam\b|surveillance|baby monitor|"
                        r"(?:indoor|outdoor|wireless|pan.tilt)[^$]{0,40}\bcam(?:era)?s?\b"),
    ("robot vacuum", r"robot(?:ic)? vac|roomba|robovac"),
    ("vacuum", r"\bvacuum|\bstick vac|carpet cleaner"),
    ("earbuds and headphones", r"earbuds?|headphones?|airpods|headset|\bbuds\b"),
    ("speaker", r"\bspeakers?\b|soundbar|sound bar"),
    ("tv", r"\btvs?\b|television|\boled\b|\bqled\b"),
    ("streaming device", r"fire tv stick|streaming stick|\broku\b|chromecast|apple tv"),
    ("monitor", r"\bmonitors?\b"),
    ("laptop", r"laptops?|notebook|macbook|chromebook"),
    ("desktop", r"desktop|mini pc|\bimac\b|gaming pc"),
    ("tablet", r"\btablets?\b|\bipad\b|e-?reader|kindle"),
    ("phone", r"\biphone\b|smartphone|galaxy s\d|\bpixel \d|cell ?phone"),
    ("smartwatch", r"smart ?watch|apple watch|fitness tracker|\bfitbit\b"),
    ("game console", r"\bps5\b|playstation 5|xbox series|nintendo switch|steam deck"),
    ("video game", r"\(ps[45]\)|\(xbox|\(switch\)|video game|digital code"),
    ("storage", r"\bssd\b|hard drive|\bhdd\b|microsd|sd card|flash drive|\bnvme\b"),
    ("pc part", r"graphics card|\bgpu\b|\brtx ?\d|motherboard|\bram\b|processor|\bcpu\b|power supply"),
    ("keyboard and mouse", r"keyboard|\bmouse\b|\bmice\b"),
    ("networking", r"router|mesh wi-?fi|wi-?fi \d|range extender|\bmodem\b"),
    ("printer", r"printer|\bink\b|toner"),
    ("camera", r"mirrorless|\bdslr\b|action cam|\blens\b|\bdrone"),
    ("smart home", r"smart plug|smart bulb|smart lock|thermostat|smart display|echo (?:dot|show|pop)|light strip"),
    ("charger and cable", r"charger|power bank|\bcable\b|charging|usb-?c hub|power strip|surge protector"),
    ("power station", r"power station|solar generator|solar panel|\bgenerator\b|\binverter\b|portable power"),
    ("coffee", r"coffee|espresso|\bk-?cups?\b|keurig|nespresso"),
    ("kitchen appliance", r"air fryer|blender|instant pot|pressure cooker|toaster|microwave|stand mixer|"
                          r"food processor|rice cooker|slow cooker|\bgrill\b|griddle|\bsmoker\b"),
    ("cookware", r"cookware|skillet|frying pan|dutch oven|knife set|\bknives\b|bakeware"),
    ("large appliance", r"refrigerator|\bfridge\b|\bwasher\b|\bdryer\b|dishwasher|\brange\b|freezer"),
    ("air and climate", r"air purifier|humidifier|dehumidifier|space heater|air conditioner|\bfan\b"),
    ("mattress and bedding", r"mattress|pillow|sheet set|comforter|duvet|\bbedding\b"),
    ("furniture", r"\bsofa\b|\bcouch\b|\bdesk\b|office chair|gaming chair|bookcase|dresser|\bbed frame\b|recliner"),
    ("power tool", r"\bdrill\b|impact driver|\bsaw\b|\bsander\b|tool kit|tool set|combo kit|\bwrench|"
                   r"leaf blower|lawn mower|string trimmer|pressure washer|chainsaw"),
    ("shoes", r"\bshoes?\b|sneakers?|\bboots?\b|sandals?|running shoe"),
    ("clothing", r"jacket|hoodie|\bt-?shirts?\b|\bjeans\b|\bpants\b|\bshorts\b|\bsocks\b|\bdress\b|sweater|"
                 r"leggings|underwear"),
    ("luggage and bags", r"luggage|suitcase|backpack|carry-?on|duffel|\btote\b"),
    ("toys", r"\blego\b|\btoys?\b|action figure|board game|\bpuzzle\b|\bnerf\b|\bdoll\b"),
    ("fitness", r"treadmill|dumbbell|exercise bike|kettlebell|yoga mat|\belliptical|resistance band"),
    ("bike and scooter", r"\be-?bike|electric scooter|\bbicycle\b|\bscooter\b"),
    ("personal care", r"toothbrush|shaver|\brazor\b|hair dryer|trimmer|skincare|sunscreen|shampoo|moisturi"),
    ("household", r"paper towels?|toilet paper|detergent|trash bags|batteries|dish soap|laundry"),
    ("grocery", r"\bsnacks?\b|\bcereal\b|protein (?:bar|powder)|\btea\b|\bcandy\b|chocolate|\bnuts\b|"
                r"sparkling water|\boz\b"),
    ("gift card", r"gift ?card"),
    ("book and media", r"\bbook\b|paperback|hardcover|blu-?ray|\bvinyl\b|audiobook"),
    ("subscription", r"subscription|membership|\d+[- ]month"),
)
STORES = (r"amazon|walmart|target|best ?buy|costco|sam'?s club|home depot|lowe'?s|ebay|newegg|woot|b&h|adorama|"
          r"macy'?s|kohl'?s|wayfair|aliexpress|temu|kroger|walgreens|cvs|rei|gamestop|staples|office depot|"
          r"dell|lenovo|hp|apple|samsung|nike|adidas|qvc|hsn|meh|tanga|bjs|bj's|micro ?center")
LEAD_STORE = re.compile(rf"^\s*(?:{STORES})(?:\.com)?\s*(?:[-–—:|]|has|offers|is offering)\s+", re.I)
PRICE = re.compile(r"\(?\s*(?:for|from|only|now|just|as low as|starting at)?\s*\$\s?\d[\d,]*(?:\.\d+)?(?:\s*(?:each|ea|/ea))?\s*\)?", re.I)
TAIL = re.compile(rf"\s+(?:at|from|via|on|@)\s+(?:{STORES})(?:\.com)?\b.*$|\s*\+\s*free.*$|\s*(?:w/|with)\s+(?:free|prime|"
                  r"s&s|subscribe|coupon|code|clip|promo).*$|\s*[-–—|]\s*(?:free|ymmv|prime|lowest|price drop|deal).*$", re.I)
PACK = re.compile(r"\(?\b(\d{1,2})\s*[- ]?\s*(?:pk|pack|count|ct|pcs?|pieces?|set of)\b\)?|\bset of (\d{1,2})\b|\b(\d{1,2})\s*x\s+(?=[A-Za-z])", re.I)
SPEC = re.compile(r"^(?:\d+(?:\.\d+)?(?:k|mp|p|w|v|a|hz|gb|tb|mb|mah|wh|oz|lb|lbs|in|inch|ft|qt|l|ml|g|kg|mm|cm|pc|pcs|ct|pk|"
                  r"x|th|nd|rd|st|fps|btu|psi|cfm|ah|kw|kwh|rpm|c|f)|\d+x\d+|\d+(?:\.\d+)?|[a-z]{1,2})$", re.I)
TIER = {"pro", "max", "plus", "ultra", "mini", "lite", "se", "air", "fe", "xl", "elite", "edge"}
GENERIC = {"the", "a", "an", "and", "for", "with", "new", "free", "shipping", "off", "deal", "sale", "pack", "now",
           "only", "select", "prime", "members", "save", "up", "to", "of", "in", "on", "by", "or", "more", "refurbished",
           "renewed", "open", "box", "wireless", "smart", "portable", "digital", "hd"}
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+&'.\-]*")


def clean(title: str) -> str:
    """The headline without the store in front, the price, and the selling words at the end."""
    t = " ".join(str(title or "").split())
    t = LEAD_STORE.sub("", t)
    t = re.sub(r"^(?:[A-Za-z&]+\s){0,2}[A-Za-z&]+:\s+(?=\S+\s+\S)", "", t)  # "Security Cameras: Blink ..." -> "Blink ..."
    t = PRICE.sub(" ", t)
    t = TAIL.sub("", t)
    t = re.sub(r"\((?:[^()]*?(?:" + STORES + r")[^()]*?|ymmv|refurb\w*|renewed|open box)\)", " ", t, flags=re.I)
    return " ".join(t.split()).strip(" -–—:|,.")


def pack(title: str) -> int:
    """How many come in the box (1 when the headline does not say)."""
    m = PACK.search(str(title or ""))
    n = int(next((g for g in m.groups() if g), 1)) if m else 1
    return n if 2 <= n <= 60 else 1


def brand(title: str) -> str:
    """The maker named in a headline: a known brand wherever it stands, else a capitalised first word."""
    text = clean(title)
    low = " " + re.sub(r"[^a-z0-9+&'\-]+", " ", text.lower()) + " "
    found = [(low.find(f" {b} "), b) for b in BRANDS if f" {b} " in low]
    if found:
        return min(found)[1].replace("black decker", "black+decker").replace("levis", "levi's")
    for w in WORD.findall(text)[:3]:
        plain = w.strip(".'").lower()
        if w[:1].isupper() and not re.search(r"\d", w) and len(plain) >= 3 and plain not in GENERIC \
                and not SPEC.match(plain) and not PACK.fullmatch(w):
            return plain
        if not PACK.search(w) and not SPEC.match(plain):
            break  # the first real word is not a name: no brand can be read
    return ""


def category(title: str) -> str:
    """The kind of thing, in plain words ("security camera"), else the broad kind (electronics, home, ...)."""
    text = clean(title)
    for name, pattern in CATEGORIES:
        if re.search(pattern, text, re.I):
            return name
    from .shopper import kind_of  # noqa: PLC0415
    return kind_of(text)


def product(title: str) -> str:
    """The product's name as it is filed: brand, then its model codes, else its first telling words; a multi-pack
    gets "x<count>" so its price is never compared with a single one's."""
    text = clean(title)
    if not text:
        return ""
    maker = brand(text)
    words = [w.strip(".'+") for w in WORD.findall(PACK.sub(" ", text))]
    spec = lambda w: bool(SPEC.match(w.lower()) or re.match(r"^\d+(?:\.\d+)?-[a-z]+$", w.lower()))  # noqa: E731
    rest = [w for w in words if w.lower() not in maker.split() and w.lower() != maker]
    codes = []
    for i, w in enumerate(rest):
        if re.search(r"\d", w) and re.search(r"[A-Za-z]", w) and not spec(w):
            code = w.lower()
            if i + 1 < len(rest) and rest[i + 1].lower() in TIER:
                code += " " + rest[i + 1].lower()
            codes.append(code)
        if len(codes) == 2:
            break
    if codes:
        name = " ".join(codes)
    else:
        telling = [w.lower() for w in rest if w.lower() not in GENERIC and (not spec(w) or w.isdigit())]
        name = " ".join(list(dict.fromkeys(telling))[:4])
    out = " ".join(p for p in (maker, name) if p) or text.lower()
    count = pack(title)
    return (out + (f" x{count}" if count > 1 else ""))[:120]


def identity(title: str) -> dict:
    return {"product": product(title), "brand": brand(title), "category": category(title)}


# --- travel ---------------------------------------------------------------------------------------------------
AIRLINES = ("american airlines", "delta", "united", "southwest", "jetblue", "alaska airlines", "spirit", "frontier",
            "hawaiian airlines", "allegiant", "sun country", "breeze", "avelo", "air canada", "westjet", "aeromexico",
            "volaris", "copa", "avianca", "latam", "british airways", "virgin atlantic", "lufthansa", "air france",
            "klm", "iberia", "tap air portugal", "swiss", "austrian", "sas", "finnair", "icelandair", "norwegian",
            "aer lingus", "turkish airlines", "emirates", "qatar airways", "etihad", "singapore airlines",
            "cathay pacific", "japan airlines", "ana", "korean air", "asiana", "eva air", "china airlines", "qantas",
            "air new zealand", "air india", "ethiopian", "ryanair", "easyjet", "wizz air", "vueling", "condor",
            "french bee", "play", "zipair", "level", "american", "alaska", "hawaiian", "turkish", "qatar")
CHAINS = ("marriott", "hilton", "hyatt", "ihg", "holiday inn", "intercontinental", "sheraton", "westin", "w hotels",
          "ritz-carlton", "ritz carlton", "st. regis", "four seasons", "fairmont", "sofitel", "novotel", "accor",
          "wyndham", "best western", "radisson", "choice hotels", "comfort inn", "hampton", "doubletree", "embassy suites",
          "courtyard", "residence inn", "kimpton", "loews", "omni", "mgm", "caesars", "wynn", "venetian", "disney",
          "universal", "sandals", "club med", "motel 6", "la quinta", "drury", "aloft", "moxy", "citizenm", "selina")
CABINS = (("first", r"\bfirst[- ]class\b|\bin first\b"), ("business", r"\bbusiness\b|lie-?flat|polaris|delta one|mint\b"),
          ("premium economy", r"premium[_ ]econom|comfort\+|premium select|economy plus"),
          ("economy", r"\beconomy\b|\bcoach\b|main cabin|\bbasic\b"))
NOT_AIRLINES = {"best", "google_history", "travelpayouts", "google", "serpapi", "duffel", "amadeus", "kiwi", "deal email",
                "deal feed",
                "deal notification", "call follow-up", "price watch"}


# A sender that is not on the list of airlines is one only if its name says so ("Azul Airlines", "Korean Air").
# A deal service, a search site or a newsletter is not: its fares are filed under no airline.
LIKE_AIRLINE = re.compile(r"\b(?:air|airlines?|airways|jet\w*|aero\w*|avia\w*|wings)\b", re.I)
NOT_AN_AIRLINE = re.compile(r"deal|fare|cheap|watch|secret|alert|google|flights?\b|travel|trips?\b|vacation|holiday|club|"
                            r"going|kayak|expedia|hopper|skyscanner|priceline|orbitz|travelzoo|news|team", re.I)


def is_airline(name: str) -> bool:
    low = " ".join(str(name or "").lower().split())
    return bool(low) and (low in AIRLINES or bool(LIKE_AIRLINE.search(low) and not NOT_AN_AIRLINE.search(low)))


def _named(names: tuple, text: str) -> str:
    low = " " + re.sub(r"[^a-z0-9+&'.\-]+", " ", str(text or "").lower()) + " "
    found = [(low.find(f" {n} "), -len(n), n) for n in names if f" {n} " in low]
    return min(found)[2] if found else ""


def cabin(text: str) -> str:
    """The cabin named in some words, else "not stated" (never guessed: most fares shown without one are economy,
    but a price filed under the wrong cabin is worse than one filed under none)."""
    for name, pattern in CABINS:
        if re.search(pattern, str(text or ""), re.I):
            return name
    return "not stated"


def travel(domain: str, item: str, seller: str = "", title: str = "", cabin_class: str = "") -> dict:
    """A flight's or a hotel's filing: {"product", "brand", "category", "place"} (see the note at the top)."""
    name = " ".join(str(item or "").lower().split())
    who = str(seller or "")
    who = who.split(":", 1)[1] if ":" in who else ("" if who.lower() in NOT_AIRLINES else who)
    if domain == "flight":
        codes = re.findall(r"[a-z]{3}", name) if re.fullmatch(r"[a-z]{3}\s*[-–>]+\s*[a-z]{3}", name) else []
        sender = " ".join(who.lower().split())[:40]
        airline = _named(AIRLINES, who) or _named(AIRLINES, title) or (sender if is_airline(sender) else "")
        return {"product": "-".join(codes) if codes else name, "place": codes[1] if codes else "",
                "brand": {"american": "american airlines", "alaska": "alaska airlines", "hawaiian": "hawaiian airlines",
                          "turkish": "turkish airlines", "qatar": "qatar airways"}.get(airline, airline),
                "category": cabin(cabin_class) if cabin_class else cabin(title)}
    place = name.split(":", 1)[1].strip() if name.startswith("hotels:") else ""
    return {"product": name, "place": place, "brand": _named(CHAINS, who) or _named(CHAINS, title) or _named(CHAINS, name),
            "category": "hotel"}
