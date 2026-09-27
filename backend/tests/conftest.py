import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BUDDY_DB", ":memory:")
os.environ.setdefault("BUDDY_MORNING_BRIEF", "")
