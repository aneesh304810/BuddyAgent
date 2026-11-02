"""Buddy's wallet: allowances, and asking before spending more.

Three allowances, all approved extensions included:
  - monthly budget   settings.monthly_budget_usd, never above settings.hard_cap_usd
  - daily allowance  the monthly budget spread over the month
  - Listen-in        settings.listen_daily_usd a day

When one runs out, or an action costs more than settings.approve_over_usd,
Buddy asks. The answer is read here, in code, from the user's own next
message: Claude can never approve spending, and a yes costs nothing.
"""

import calendar
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from . import usage
from .config import settings
from .memory import Memory
from .tools import local_now

PENDING_TTL_SECONDS = 15 * 60

SPENDING_TOOL = {
    "name": "spending_status",
    "description": "How much Buddy has cost: today, yesterday, this month, against the "
    "allowances and budget. Use when the user asks about cost or before suggesting something pricey.",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

YES = re.compile(
    r"^(yes|yeah|yep|yup|ok|okay|sure|go ahead|go for it|do it|please do|approved?|fine|"
    r"haan|ha|haa|theek hai|thik hai|chalo|kar do|"           # Hindi
    r"s[ií]|dale|claro|vale|"                                  # Spanish
    r"sim|pode|beleza)\b",                                     # Portuguese
    re.IGNORECASE,
)
# Phrases that start with "no" but mean yes.
YES_PHRASES = re.compile(r"^(no problem|no worries|why not|of course)\b", re.IGNORECASE)
NO = re.compile(
    r"\b(no|nope|nah|don'?t|do not|stop|cancel|not now|later|skip|"
    r"nahi|nahin|mat|n[ãa]o|todav[ií]a no)\b",
    re.IGNORECASE,
)


def answer_of(text: str) -> bool | None:
    """True for a clear yes, False for a clear no, None otherwise."""
    words = re.sub(r"^(buddy|hey buddy)[,\s]+", "", text.strip(), flags=re.IGNORECASE)
    words = words.strip(" .!?,").lower()
    if not words:
        return None
    if YES_PHRASES.match(words):
        return True
    if NO.search(words):
        return False
    if YES.match(words) and len(words.split()) <= 8:
        return True
    return None


@dataclass
class Pending:
    id: str
    kind: str                 # month | day | listen | action
    question: str
    turn: int                 # the user turn it was asked in; a yes must come later
    created: float
    run: Callable[[], Awaitable[str]] | None = None
    resume: str | None = None  # the request to answer once approved
    group: str = ""           # actions from the same tool in the same turn are asked together
    item: str = ""            # this action in a few words, for a combined question
    plural: str = ""          # what several of them are called ("email rules")


class Thrift:
    """Questions wait in a queue: one is asked at a time, the next once it's answered. Several
    actions from the same tool in one turn (6 email rules) become one question, and one yes runs them all."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.queue: list[Pending] = []
        self.turn = 0  # every user message, from any device

    @property
    def pending(self) -> Pending | None:
        """The question being asked now (the front of the queue)."""
        self._expire()
        return self.queue[0] if self.queue else None

    def _expire(self) -> None:
        now = time.monotonic()
        self.queue = [p for p in self.queue if now - p.created <= PENDING_TTL_SECONDS]

    def _batch(self) -> list[Pending]:
        """The front question and any queued with it: same tool, same turn."""
        if not self.queue:
            return []
        first = self.queue[0]
        if first.kind != "action" or not first.group:
            return [first]
        out = [first]
        for p in self.queue[1:]:
            if p.kind == "action" and p.group == first.group and p.turn == first.turn:
                out.append(p)
            else:
                break
        return out

    def question(self) -> str:
        """What to ask the user now: one question, or the batch as one."""
        batch = self._batch()
        if not batch:
            return ""
        if len(batch) == 1:
            return batch[0].question
        noun = batch[0].plural or "things"
        items = "; ".join(f"{i}) {p.item or p.question.rstrip('?')}" for i, p in enumerate(batch, 1))
        return f"Shall I set these {len(batch)} {noun}: {items}? One yes does them all."

    # --- money -----------------------------------------------------------
    def _today(self) -> str:
        return local_now().date().isoformat()

    def _month(self) -> str:
        return local_now().strftime("%Y-%m")

    def _extra(self, key: str) -> float:
        value = self.memory.get(f"extra:{key}")
        return float(value) if value else 0.0

    def _grant(self, key: str, amount: float) -> None:
        self.memory.set(f"extra:{key}", str(round(self._extra(key) + amount, 4)))

    def hard_cap(self) -> float:
        return settings.hard_cap_usd or settings.monthly_budget_usd * 2

    def month_budget(self) -> float:
        return min(settings.monthly_budget_usd + self._extra(f"month:{self._month()}"), self.hard_cap())

    def month_spent(self) -> float:
        return usage.month_to_date(self.memory)

    def base_daily(self) -> float:
        now = local_now()
        days = calendar.monthrange(now.year, now.month)[1]
        return settings.daily_allowance_usd or round(settings.monthly_budget_usd / days, 2)

    def daily_allowance(self) -> float:
        return self.base_daily() + self._extra(f"day:{self._today()}")

    def today_spent(self) -> float:
        return usage.spent_on(self.memory, self._today())

    def listen_allowance(self) -> float:
        return settings.listen_daily_usd + self._extra(f"listen:{self._today()}")

    def listen_spent(self) -> float:
        return usage.spent_on(self.memory, self._today(), "listen")

    def status(self) -> dict:
        yesterday = (local_now() - timedelta(days=1)).date().isoformat()
        return {
            "today_usd": round(self.today_spent(), 2),
            "daily_allowance_usd": round(self.daily_allowance(), 2),
            "listen_in_today_usd": round(self.listen_spent(), 2),
            "listen_in_allowance_usd": round(self.listen_allowance(), 2),
            "yesterday_usd": round(usage.spent_on(self.memory, yesterday), 2),
            "month_usd": round(self.month_spent(), 2),
            "month_budget_usd": round(self.month_budget(), 2),
            "hard_cap_usd": round(self.hard_cap(), 2),
            "approval_needed_over_usd": settings.approve_over_usd,
        }

    def summary(self) -> str:
        s = self.status()
        return (
            f"Yesterday ${s['yesterday_usd']:.2f}; today so far ${s['today_usd']:.2f} of "
            f"${s['daily_allowance_usd']:.2f}; this month ${s['month_usd']:.2f} of ${s['month_budget_usd']:.2f}."
        )

    # --- asking ----------------------------------------------------------
    def _ask(self, kind: str, question: str, run=None, resume: str | None = None, group: str = "",
             item: str = "", plural: str = "") -> str:
        p = Pending(id=secrets.token_hex(3), kind=kind, question=question, turn=self.turn,
                    created=time.monotonic(), run=run, resume=resume, group=group, item=item, plural=plural)
        self._expire()
        if kind == "action":
            self.queue.append(p)  # after anything already waiting
        else:  # money and Listen-in questions come first, and only once each
            self.queue = [p] + [q for q in self.queue if q.kind != kind]
        return question

    def gate_chat(self, user_text: str) -> str | None:
        """Before answering: a question to ask instead, if an allowance is used up."""
        if self.month_spent() >= self.month_budget():
            if self.month_budget() >= self.hard_cap():
                return (
                    f"We've reached your hard cap of ${self.hard_cap():.0f} for this month, so I'm pausing. "
                    "Only you can raise BUDDY_HARD_CAP_USD on the server."
                )
            add = min(settings.topup_usd, self.hard_cap() - self.month_budget())
            return self._ask(
                "month",
                f"Heads up, we've used this month's ${self.month_budget():.0f} budget. "
                f"Want to add ${add:.0f} so I can keep helping? Yes or no.",
                resume=user_text,
            )
        if self.today_spent() >= self.daily_allowance():
            return self._ask(
                "day",
                f"Quick money check: we've spent ${self.today_spent():.2f} today, over the "
                f"${self.daily_allowance():.2f} daily allowance. Keep going today? Each yes adds "
                f"another ${self.base_daily():.2f}.",
                resume=user_text,
            )
        return None

    def listen_allowed(self) -> tuple[bool, str | None]:
        """Listen-in may spend more today? If not, the whisper to send (once)."""
        if self.month_spent() >= self.month_budget():
            return False, None  # the monthly question comes up when the user next talks
        if self.listen_spent() < self.listen_allowance():
            return True, None
        if any(p.kind == "listen" for p in self.queue):
            return False, None  # already asked
        return False, self._ask(
            "listen",
            f"Listen-in has used today's ${self.listen_allowance():.2f}. "
            f"Say \"Buddy, yes\" to keep listening today for another ${settings.listen_daily_usd:.2f}.",
        )

    def ask_action(self, question: str, run: Callable[[], Awaitable[str]], group: str = "", item: str = "",
                   plural: str = "") -> str:
        """For an action that needs a yes: queue it. group/item/plural let several of the same kind from one
        turn be asked as one question ("Shall I set these 6 email rules: ...?")."""
        self._ask("action", question, run=run, group=group, item=item, plural=plural)
        batch = self._batch()
        now = self.question()
        if any(p.run is run for p in batch) and len(batch) > 1:
            return ("APPROVAL NEEDED (this replaces any earlier approval question in this reply). Ask the user "
                    f"exactly this, then stop: \"{now}\" Their answer is handled automatically; don't call this "
                    "tool again.")
        if batch and batch[0].run is run:
            return ("APPROVAL NEEDED. Ask the user exactly this, then stop: "
                    f"\"{now}\" Their answer is handled automatically; don't call this tool again.")
        return ("QUEUED for approval after the current question. Don't ask it now: ask only "
                f"\"{now}\". Buddy asks the next one by itself once they answer.")

    async def resolve(self, user_text: str, trusted: bool = True) -> tuple[str | None, str | None]:
        """Read the user's reply to a pending question.

        Returns (what to say, request to answer next). (None, None) means there
        was nothing to decide and the message should be answered normally.
        trusted=False: the words came through Listen-in ("Hey Buddy, yes"), where
        anyone in the room could have said them, so a yes doesn't count.
        """
        self.turn += 1
        p = self.pending  # also drops questions that waited too long
        if p is None or p.turn >= self.turn:
            return None, None
        answer = answer_of(user_text)
        if answer is None:
            return None, None  # a new topic; the question stays open for a while
        if answer is True and not trusted:
            return ("To be safe, I only take a yes for that from you directly: tap Approve, or say it "
                    "in Talk mode."), None
        batch = self._batch()
        self.queue = self.queue[len(batch):]
        said, resume = await self._settle(p, batch, answer)
        nxt = self.question()
        if nxt:  # the next waiting question: asked now, answered by the user's next message
            said = f"{said} Next: {nxt}" if said else nxt
        return said, resume

    async def _settle(self, p: Pending, batch: list[Pending], answer: bool) -> tuple[str, str | None]:
        if answer is False and len(batch) > 1:
            return f"Okay, I won't do any of those {len(batch)}.", None
        if answer is True and len(batch) > 1:
            done = []
            for b in batch:
                try:
                    done.append((await b.run()) if b.run else "Done.")
                except Exception as e:  # one failing doesn't stop the rest
                    done.append(f"Couldn't do '{b.item or b.question}': {type(e).__name__}.")
            return " ".join(done), None
        if answer is False:
            if p.kind in ("month", "day"):
                return "Okay, I'll hold off for now. Reminders keep working.", None
            if p.kind == "listen":
                return "Okay, Listen-in will stay quiet for the rest of today.", None
            return "Okay, I won't do that.", None
        if p.kind == "month":
            add = min(settings.topup_usd, self.hard_cap() - self.month_budget())
            self._grant(f"month:{self._month()}", add)
            # A yes to more this month also covers today; don't ask twice in a row.
            short = max(0.0, self.today_spent() - self.daily_allowance())
            self._grant(f"day:{self._today()}", short + self.base_daily())
            return f"Done, this month's budget is now ${self.month_budget():.0f}.", p.resume
        if p.kind == "day":
            self._grant(f"day:{self._today()}", self.base_daily())
            return f"Okay, today's allowance is now ${self.daily_allowance():.2f}.", p.resume
        if p.kind == "listen":
            self._grant(f"listen:{self._today()}", settings.listen_daily_usd)
            return "Okay, I'll keep listening today.", None
        return (await p.run()) if p.run else "Done.", None
