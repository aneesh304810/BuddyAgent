"""The shape every connector follows."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


@dataclass
class Record:
    """One item from an app: an event, an email, a stay, a transaction..."""

    external_id: str             # the app's own id, so re-syncing never duplicates
    kind: str                    # event | email | ...
    title: str
    body: str = ""
    start_at: datetime | None = None  # timezone-aware
    end_at: datetime | None = None
    data: dict = field(default_factory=dict)


class Connector:
    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    # Settings it needs from /etc/buddy/buddy.env, with a plain-words hint for each.
    required: ClassVar[dict[str, str]] = {}
    every_minutes: ClassVar[int] = 30
    # True: each sync is the full picture (a calendar window), so items that vanished
    # are removed. False: syncs add new items (email).
    snapshot: ClassVar[bool] = False
    keep_days: ClassVar[int] = 30

    def __init__(self, env: Mapping[str, str] | None = None):
        self.env = os.environ if env is None else env

    def setting(self, key: str, default: str = "") -> str:
        return (self.env.get(key) or default).strip()

    def missing(self) -> list[str]:
        return [key for key in self.required if not self.setting(key)]

    def configured(self) -> bool:
        return not self.missing()

    async def fetch(self) -> list[Record]:
        raise NotImplementedError
