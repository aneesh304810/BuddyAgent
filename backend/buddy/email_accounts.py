"""All of the user's email accounts, read from the settings file.

Account 1 uses BUDDY_EMAIL_ADDRESS and BUDDY_EMAIL_APP_PASSWORD; accounts 2 to 9
use BUDDY_EMAIL_2_ADDRESS, BUDDY_EMAIL_2_APP_PASSWORD and so on. Servers are
found from the address (Gmail, Yahoo, iCloud...); BUDDY_EMAIL[_N]_IMAP_HOST,
_SMTP_HOST, _SMTP_PORT and _FOLDER override them. Google Workspace addresses
on your own domain work like Gmail, which is also the default for unknown domains.
"""

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
    raise LookupError(f"Which account? I have: {names}." if matches else f"No account matches '{which}'. I have: {names}.")
