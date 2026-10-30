"""Buddy, your personal AI companion."""

import os
from pathlib import Path


def _load_sealed_settings() -> None:
    """Settings encrypted at rest (deploy/security.sh seal): systemd decrypts them into this
    service's private, in-memory credentials folder. Loaded before any module reads a setting."""
    folder = os.environ.get("CREDENTIALS_DIRECTORY")
    path = Path(folder, "buddy-env") if folder else None
    if not path or not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


_load_sealed_settings()
