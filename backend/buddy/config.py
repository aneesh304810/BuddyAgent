"""Runtime settings, read from environment variables (or a .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    user_name: str = os.getenv("BUDDY_USER_NAME", "friend")
    assistant_name: str = os.getenv("BUDDY_NAME", "Buddy")
    timezone: ZoneInfo = ZoneInfo(os.getenv("BUDDY_TIMEZONE", "UTC"))
    # Shared secret the phone must send to connect. Required once the server is public.
    access_token: str = os.getenv("BUDDY_TOKEN", "")
    # Languages the user understands; Listen-in only translates everything else.
    known_languages: str = os.getenv(
        "BUDDY_KNOWN_LANGUAGES", "English, Hindi, Malayalam, Marathi, Tamil"
    )
    db_path: Path = Path(os.getenv("BUDDY_DB", "buddy.db"))
    # Economy by default: everyday chat, reminders and briefings run on Sonnet
    # (about half the price); research and Listen-in decisions on Opus.
    # Set BUDDY_CHAT_MODEL=claude-opus-5-5 to use Opus for everything.
    model: str = os.getenv("BUDDY_MODEL", "claude-opus-5-5")
    chat_model: str = os.getenv("BUDDY_CHAT_MODEL", "claude-sonnet-5-5")
    # Listen-in: a cheap first pass decides whether anything needs attention;
    # only then does the listen model read it. Empty triage = no first pass.
    listen_model: str = os.getenv("BUDDY_LISTEN_MODEL", os.getenv("BUDDY_MODEL", "claude-opus-5-5"))
    listen_triage_model: str = os.getenv("BUDDY_LISTEN_TRIAGE_MODEL", "claude-haiku-5-5")
    # Gather this many seconds of room speech per check (0 = check every phrase).
    listen_batch_seconds: float = float(os.getenv("BUDDY_LISTEN_BATCH_SECONDS", "20"))
    # Voice replies must be quick; "low" effort keeps thinking short.
    effort: str = os.getenv("BUDDY_EFFORT", "low")
    # Local time for the daily morning briefing, "HH:MM". Empty disables it.
    morning_brief_at: str = os.getenv("BUDDY_MORNING_BRIEF", "08:00")
    # Stop calling Claude once this month's estimated spend reaches this (USD).
    monthly_budget_usd: float = float(os.getenv("BUDDY_MONTHLY_BUDGET_USD", "40"))
    # Thrift: Buddy asks before going past any of these. A yes adds topup_usd to the
    # month, but never beyond the hard cap (default: twice the monthly budget).
    hard_cap_usd: float = float(os.getenv("BUDDY_HARD_CAP_USD", "0"))
    topup_usd: float = float(os.getenv("BUDDY_TOPUP_USD", "10"))
    # Daily allowance; 0 = the monthly budget spread evenly over the month.
    daily_allowance_usd: float = float(os.getenv("BUDDY_DAILY_ALLOWANCE_USD", "0"))
    listen_daily_usd: float = float(os.getenv("BUDDY_LISTEN_DAILY_USD", "0.50"))
    # Single actions estimated above this (e.g. a deep research job) need a yes.
    approve_over_usd: float = float(os.getenv("BUDDY_APPROVE_OVER_USD", "0.50"))
    agents_file: Path = Path(os.getenv("BUDDY_AGENTS", "agents.yaml"))
    # Background jobs (research and others) running at the same time.
    max_parallel_jobs: int = int(os.getenv("BUDDY_MAX_PARALLEL_JOBS", "2"))
    # Research jobs allowed per day, and the most one job may spend (USD).
    research_daily_limit: int = int(os.getenv("BUDDY_RESEARCH_DAILY_LIMIT", "10"))
    research_max_usd: float = float(os.getenv("BUDDY_RESEARCH_MAX_USD", "5"))
    web_dir: Path = Path(__file__).resolve().parents[2] / "web"


settings = Settings()
