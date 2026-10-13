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
    r"clave|token)\b([^\d\n]{0,15}?)(\d{3,8})\b",
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


def _card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group())
    if not _luhn_ok(digits):
        return match.group()  # not a real card number; the account rule may still apply
    return f"card ****{digits[-4:]}"


def mask(text: str) -> str:
    """Return `text` with cards, accounts, national IDs, codes and passwords masked."""
    if not text:
        return text
    text = _CODE.sub(lambda m: f"{m.group(1)}{m.group(2)}[code hidden]", text)
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
