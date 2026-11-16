import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BUDDY_DEV", "1")  # no token in tests
os.environ.setdefault("BUDDY_SMART_ADDRESSING", "0")  # its own tests switch it on
os.environ.setdefault("BUDDY_DB", ":memory:")
os.environ.setdefault("BUDDY_MORNING_BRIEF", "")
os.environ.setdefault("BUDDY_TRENDS", "0")  # the daily trends digest would join test conversations; its tests call it directly
os.environ.setdefault("BUDDY_HEALTH_DIGEST", "0")  # the daily digest would join test conversations; its tests call it directly
os.environ.setdefault("BUDDY_AGENTS", "tests/no-agents.yaml")
# Listen-in tests check every phrase straight away; the batching and triage
# tests switch those on themselves.
os.environ.setdefault("BUDDY_LISTEN_BATCH_SECONDS", "0")
os.environ.setdefault("BUDDY_LISTEN_TRIAGE_MODEL", "")
os.environ.pop("BUDDY_DB_KEY", None)
os.environ.setdefault("BUDDY_MEANING_SEARCH", "0")  # no model download in tests; brain tests pass a stand-in
os.environ.setdefault("BUDDY_MONEY_WEEKLY", "")  # the scheduled money reports; their tests call them directly
os.environ.setdefault("BUDDY_MONEY_MONTHLY", "")
os.environ.setdefault("BUDDY_SOC_MONTHLY", "")
os.environ.setdefault("BUDDY_CARD_LOOKUP", "")
os.environ.setdefault("BUDDY_CREDIT_DEALS", "")  # no web lookups from the background loop in tests  # the monthly SOC readiness report; its tests call it directly
os.environ.setdefault("BUDDY_COMPANY_DOCS", "")  # its tests sync on their own


import pytest  # noqa: E402

from buddy import bip_sources  # noqa: E402

bip_sources.PACED = False  # no real source is reached in tests, so nothing needs pacing
from buddy import fundamentals  # noqa: E402

fundamentals.PACED = False


# No test may reach the internet: a real call makes the run slow and its result depend on someone else's
# server. Anything that is not this machine is refused at once, the way an unreachable host would be.
import socket  # noqa: E402

_LOCAL = {"localhost", "127.0.0.1", "::1", "testserver", ""}
_real_getaddrinfo, _real_connect = socket.getaddrinfo, socket.socket.connect


def _local_only_lookup(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else str(host or "")
    if name not in _LOCAL:
        raise socket.gaierror(socket.EAI_NONAME, f"tests do not reach the internet ({name})")
    return _real_getaddrinfo(host, *args, **kwargs)


def _local_only_connect(self, address):
    if isinstance(address, tuple) and str(address[0]) not in _LOCAL:
        raise ConnectionRefusedError(f"tests do not reach the internet ({address[0]})")
    return _real_connect(self, address)


socket.getaddrinfo = _local_only_lookup
socket.socket.connect = _local_only_connect


@pytest.fixture(autouse=True)
def _no_lockouts():
    """The brute-force lockout counts failed sign-ins across requests: each test starts clean."""
    from buddy import guard

    guard.guard.reset()
    yield


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Buddy Connect counts calls per minute in the process: each test starts clean."""
    from buddy import gateway

    gateway._recent.clear()
    yield


@pytest.fixture(autouse=True)
def _no_claude_cutoff(monkeypatch):
    """The monthly Claude cutoff (budgets.py) is off unless a test sets one."""
    monkeypatch.setenv("BUDDY_CLAUDE_KILL_USD", "0")
