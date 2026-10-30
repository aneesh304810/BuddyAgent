import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BUDDY_DEV", "1")  # no token in tests
os.environ.setdefault("BUDDY_SMART_ADDRESSING", "0")  # its own tests switch it on
os.environ.setdefault("BUDDY_DB", ":memory:")
os.environ.setdefault("BUDDY_MORNING_BRIEF", "")
os.environ.setdefault("BUDDY_HEALTH_DIGEST", "0")  # the daily digest would join test conversations; its tests call it directly
os.environ.setdefault("BUDDY_AGENTS", "tests/no-agents.yaml")
# Listen-in tests check every phrase straight away; the batching and triage
# tests switch those on themselves.
os.environ.setdefault("BUDDY_LISTEN_BATCH_SECONDS", "0")
os.environ.setdefault("BUDDY_LISTEN_TRIAGE_MODEL", "")
os.environ.pop("BUDDY_DB_KEY", None)
