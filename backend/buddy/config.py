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
    model: str = os.getenv("BUDDY_MODEL", "claude-opus-5")
    # Voice replies must be quick; "low" effort keeps thinking short.
    effort: str = os.getenv("BUDDY_EFFORT", "low")
    # Local time for the daily morning briefing, "HH:MM". Empty disables it.
    morning_brief_at: str = os.getenv("BUDDY_MORNING_BRIEF", "08:00")
    # Stop calling Claude once this month's estimated spend reaches this (USD).
    monthly_budget_usd: float = float(os.getenv("BUDDY_MONTHLY_BUDGET_USD", "40"))
    agents_file: Path = Path(os.getenv("BUDDY_AGENTS", "agents.yaml"))
    web_dir: Path = Path(__file__).resolve().parents[2] / "web"


settings = Settings()
