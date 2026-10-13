"""Mask sensitive numbers before any text leaves the server for Claude.

Card numbers keep their last four digits ("card ****4521") so Buddy can still
tell cards apart; everything else is replaced outright. Masking is plain code,
so it is predictable and works without any AI model.
"""

import re

# Aadhaar: 12 digits in 4-4-4 groups, first digit 2-9.
_AADHAAR = re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")
# PAN: 5 letters, 4 digits, 1 letter.
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# Card-like digit runs: 13-19 digits, optionally grouped by spaces or dashes.
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
# One-time passwords, CVVs and PINs: a short number right after its keyword.
_CODE = re.compile(
    r"\b(OTP|one[- ]time (?:password|code)|verification code|CVV|CVC|PIN(?!\s*code)|"
    r"passcode|security code)\b([^\d\n]{0,15}?)(\d{3,8})\b",
    re.IGNORECASE,
)
# Passwords given in plain words: "password is hunter2", "password: hunter2".
_PASSWORD = re.compile(r"\b(password|passwd|pwd)(\s*(?:is|:|=)\s*)(\S+)", re.IGNORECASE)
# Bank account numbers: 9-18 digit runs (after cards and Aadhaar are handled).
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
    """Return `text` with card, account, Aadhaar, PAN, OTP/CVV/PIN and passwords masked."""
    if not text:
        return text
    text = _CODE.sub(lambda m: f"{m.group(1)}{m.group(2)}[code hidden]", text)
    text = _PASSWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}[password hidden]", text)
    text = _CARD.sub(_card, text)
    text = _AADHAAR.sub("[Aadhaar hidden]", text)
    text = _PAN.sub("[PAN hidden]", text)
    text = _ACCOUNT.sub(lambda m: f"account ****{m.group()[-4:]}", text)
    return text
