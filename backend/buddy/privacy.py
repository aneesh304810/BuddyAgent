"""Mask sensitive numbers before any text leaves the server for Claude.

Covers the USA, India and South America. Card numbers keep their last four
digits ("card ****4521") so Buddy can still tell cards apart; everything else
is replaced outright. Masking is plain code, so it is predictable and works
without any AI model.
"""

import re

_I = re.IGNORECASE

# --- India ---------------------------------------------------------------
# Aadhaar: 12 digits in 4-4-4 groups, first digit 2-9.
_AADHAAR = re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")
# PAN: 5 letters, 4 digits, 1 letter.
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")

# --- USA -----------------------------------------------------------------
# SSN / ITIN written as 123-45-6789 (or with spaces).
_SSN = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")

# --- South America -------------------------------------------------------
# Brazil CPF 123.456.789-09 and CNPJ 12.345.678/0001-95.
_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
# Chile RUT 12.345.678-9 / 12.345.678-K.
_RUT = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dkK]\b")
# Argentina CUIT/CUIL 20-12345678-3.
_CUIT = re.compile(r"\b\d{2}-\d{8}-\d\b")

# --- ID numbers named by their keyword (any country) ---------------------
# "SSN 123456789", "DNI 12.345.678", "cédula 1020304050", "passport X1234567"...
_ID_KEYWORD = re.compile(
    r"\b(SSN|social security(?: number| no\.?)?|ITIN|tax ID|EIN|DNI|C[eé]dula|CC|CPF|"
    r"RG|RUT|RUN|CUIT|CUIL|NIT|CURP|passport(?: number| no\.?)?|pasaporte|passaporte|"
    r"driver'?s licen[cs]e(?: number| no\.?)?|licencia)\b"
    r"(\W{0,6}(?:is|es|é|number|n[uú]mero|no\.?|#)?\W{0,4})([A-Z0-9][A-Z0-9.\-]{4,18}[A-Z0-9])",
    _I,
)

# --- Everywhere ----------------------------------------------------------
# Card-like digit runs: 13-19 digits, optionally grouped by spaces or dashes.
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# One-time codes, CVVs and PINs (English, Spanish, Portuguese): a short number after its keyword.
_CODE = re.compile(
    r"\b(OTP|one[- ]time (?:password|code)|verification code|security code|passcode|CVV|CVC|"
    r"PIN(?!\s*code)|c[oó]digo(?: de (?:verificaci[oó]n|seguridad|verifica[cç][aã]o|seguran[cç]a))?|"
    r"clave|token|code)\b([^\d\n]{0,15}?)(\d{3,8})\b",
    _I,
)
# "Code" alone is also a postal/phone word: leave "zip code 10001", "area code 212" alone.
_NOT_SECRET_CODE = re.compile(r"\b(zip|area|postal|post|pin|country|dial|dialing|promo|discount|coupon)\s*$", _I)
# The number first: "482913 is your verification code", "Use 1234 as your OTP".
_CODE_FIRST = re.compile(
    r"\b(\d{4,8})(\s+(?:is|as)\s+(?:your|the)\s+(?:\w+\s+){0,2}?"
    r"(?:code|OTP|passcode|password|PIN|c[oó]digo|senha|clave))\b",
    _I,
)
# Passwords in plain words: "password is hunter2", "contraseña: x", "senha é x".
_PASSWORD = re.compile(
    r"\b(password|passwd|pwd|contrase[ñn]a|senha)(\s*(?:is|es|é|:|=)\s*)(\S+)", _I
)
# Bank account and routing numbers: 9-18 digit runs (after cards and IDs are handled).
_ACCOUNT = re.compile(r"\b\d{9,18}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _code(match: re.Match) -> str:
    if match.group(1).lower() == "code" and _NOT_SECRET_CODE.search(match.string[: match.start()]):
        return match.group()
    return f"{match.group(1)}{match.group(2)}[code hidden]"


def _card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group())
    if not _luhn_ok(digits):
        return match.group()  # not a real card number; the account rule may still apply
    return f"card ****{digits[-4:]}"


def mask(text: str) -> str:
    """Return `text` with cards, accounts, national IDs, codes and passwords masked."""
    if not text:
        return text
    text = _CODE_FIRST.sub(lambda m: f"[code hidden]{m.group(2)}", text)
    text = _CODE.sub(_code, text)
    text = _PASSWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}[password hidden]", text)
    text = _ID_KEYWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}[ID hidden]", text)
    text = _CNPJ.sub("[ID hidden]", text)
    text = _CPF.sub("[ID hidden]", text)
    text = _RUT.sub("[ID hidden]", text)
    text = _CUIT.sub("[ID hidden]", text)
    text = _SSN.sub("[ID hidden]", text)
    text = _CARD.sub(_card, text)
    text = _AADHAAR.sub("[Aadhaar hidden]", text)
    text = _PAN.sub("[PAN hidden]", text)
    text = _ACCOUNT.sub(lambda m: f"account ****{m.group()[-4:]}", text)
    return text


# Keys, tokens, passwords and private addresses: never sent to free models, never logged.
SECRET_PATTERNS = [
    re.compile(r"(?i)\bhttps?://[^/\s:@]+:[^/\s@]+@"),                     # a link with a user and password
    re.compile(r"(?i)/(?:s|secret|token|key|hooks)/[A-Za-z0-9_=-]{20,}"),       # a secret in a link (Zapier MCP...)
    re.compile(r"(?i)[?&](?:key|api_key|apikey|token|access_token|secret|sig|signature)=[^&\s\"']{8,}"),
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_-]{16,}"),                     # OpenAI, Anthropic, Stripe...
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_\w{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                # AWS
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                             # Google
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),                        # Slack
    re.compile(r"\bdop_v1_[a-f0-9]{20,}"),                               # DigitalOcean
    re.compile(r"\btskey-\w+-\w{10,}"),                                  # Tailscale
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}"),             # JWT
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"(?i)\b[\w-]*(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[\w-]*"
               r"[\"']?\s*[:=]\s*[\"']?(?!\s*$)(?!(os\.|getenv|env\[|process\.env|\$\{?|<|\*{3}|x{3}|\.\.\.))[^\s\"',;]{6,}"),
    re.compile(r"(?i)\b[a-z][\w-]*\.ts\.net\b"),                         # the user's Tailscale machines
    re.compile(r"(?i)\b(postgres|postgresql|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:/]+:[^\s@/]+@"),  # URL with a password
]


def redact_secrets(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[secret hidden]", text)
    return text


class SecretFilter:
    """A logging filter: a key that ends up in a log message (an error echoing a URL or a header)
    is hidden before it's written."""

    def filter(self, record) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = redact_secrets(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True
