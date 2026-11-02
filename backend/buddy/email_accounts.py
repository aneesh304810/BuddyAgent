"""All of the user's email accounts, read from the settings file.

Account 1 uses BUDDY_EMAIL_ADDRESS and BUDDY_EMAIL_APP_PASSWORD; accounts 2 to 9
use BUDDY_EMAIL_2_ADDRESS, BUDDY_EMAIL_2_APP_PASSWORD and so on. Servers are
found from the address (Gmail, Yahoo, iCloud...); BUDDY_EMAIL[_N]_IMAP_HOST,
_SMTP_HOST, _SMTP_PORT and _FOLDER override them. Google Workspace addresses
on your own domain work like Gmail, which is also the default for unknown domains.
"""

import re
import os
from collections.abc import Mapping
from dataclasses import dataclass

MAX_ACCOUNTS = 9
GMAIL = ("imap.gmail.com", "smtp.gmail.com", 465)
MICROSOFT = ("outlook.office365.com", "smtp-mail.outlook.com", 587)
ICLOUD = ("imap.mail.me.com", "smtp.mail.me.com", 587)
PROVIDERS = {
    "gmail.com": GMAIL, "googlemail.com": GMAIL,
    "outlook.com": MICROSOFT, "hotmail.com": MICROSOFT, "live.com": MICROSOFT, "msn.com": MICROSOFT,
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465),
    "icloud.com": ICLOUD, "me.com": ICLOUD, "mac.com": ICLOUD,
}


@dataclass(frozen=True)
class EmailAccount:
    number: int
    address: str
    password: str
    imap_host: str
    smtp_host: str
    smtp_port: int
    folder: str = "INBOX"

    @property
    def is_gmail(self) -> bool:
        return self.imap_host == GMAIL[0]


def email_accounts(env: Mapping[str, str] | None = None) -> list[EmailAccount]:
    env = os.environ if env is None else env
    get = lambda key, default="": (env.get(key) or default).strip()
    accounts = []
    for n in range(1, MAX_ACCOUNTS + 1):
        prefix = "BUDDY_EMAIL_" if n == 1 else f"BUDDY_EMAIL_{n}_"
        address = get(prefix + "ADDRESS")
        password = get(prefix + "APP_PASSWORD").replace(" ", "")
        if not address or not password:
            continue
        imap, smtp, port = PROVIDERS.get(address.rsplit("@", 1)[-1].lower(), GMAIL)
        accounts.append(EmailAccount(
            number=n, address=address, password=password,
            imap_host=get(prefix + "IMAP_HOST", imap),
            smtp_host=get(prefix + "SMTP_HOST", smtp),
            smtp_port=int(get(prefix + "SMTP_PORT", str(port))),
            folder=get(prefix + "FOLDER", "INBOX"),
        ))
    return accounts


def pick_account(accounts: list[EmailAccount], which: str = "") -> EmailAccount:
    """The account the user means: '' = the first; else matched by address or part of it."""
    if not accounts:
        raise LookupError("No email account is set up yet.")
    which = which.strip().lower()
    if not which:
        return accounts[0]
    for a in accounts:
        if which == a.address.lower():
            return a
    matches = [a for a in accounts if which in a.address.lower()]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(a.address for a in accounts)
    if matches:
        raise LookupError(f"Which account? I have: {names}.")
    # Speech often mangles addresses ("aneesh nair three oh four at gmail"): match the spoken form, and if
    # one account is clearly the closest use it; otherwise ask. Never guess silently between close ones.
    spoken = spoken_address(which)
    for a in accounts:
        if spoken and spoken in (a.address.lower(), a.address.lower().split("@")[0]):
            return a
    scored = sorted(((_closeness(spoken, a.address.lower()), a) for a in accounts), key=lambda x: -x[0])
    best, second = scored[0], (scored[1] if len(scored) > 1 else (0.0, None))
    if best[0] >= 0.9 and best[0] - second[0] >= 0.15:
        return best[1]
    if best[0] >= 0.55:
        raise LookupError(f"Did you mean {best[1].address}? Ask the user to confirm before using it "
                          f"(their accounts: {names}).")
    raise LookupError(f"No account matches '{which}'. Ask the user which one: {names}.")


NUMBER_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
                "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def spoken_address(text: str) -> str:
    """'Aneesh Nair three oh four at gmail dot com' -> 'aneeshnair304@gmail.com'."""
    words = re.findall(r"[a-z0-9@._-]+", text.lower())
    out = []
    for w in words:
        if w == "at":
            out.append("@")
        elif w in ("dot", "point"):
            out.append(".")
        elif w in ("underscore",):
            out.append("_")
        elif w in ("dash", "hyphen"):
            out.append("-")
        elif w in NUMBER_WORDS and (out and (out[-1][-1:].isdigit() or out[-1][-1:].isalpha())):
            out.append(NUMBER_WORDS[w])
        else:
            out.append(w)
    joined = "".join(out)
    if joined.endswith("@gmail"):
        joined += ".com"
    return joined


def _closeness(spoken: str, address: str) -> float:
    """How close a spoken account name is to an address (0-1), on the whole address and its name part."""
    from difflib import SequenceMatcher

    local = address.split("@")[0]
    s_local = spoken.split("@")[0]
    return max(SequenceMatcher(None, spoken, address).ratio(), SequenceMatcher(None, s_local, local).ratio())
