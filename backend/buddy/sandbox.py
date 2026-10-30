"""Talking to the code sandbox service (deploy/sandbox/sandboxd.py) over its local socket.

The sandbox runs code in throwaway containers, away from Buddy: Buddy never runs a command
itself, it asks the sandbox to. Nothing here works until deploy/sandbox.sh has set it up.
"""

import asyncio
import json
import os
import re
from pathlib import Path

SOCKET = os.getenv("BUDDY_SANDBOX_SOCKET", "/run/buddy-sandbox/sandbox.sock")


class SandboxError(Exception):
    pass


def available(path: str | None = None) -> bool:
    return Path(path or SOCKET).is_socket()


def workspace_name(text: str) -> str:
    """A workspace name from a repo link or a task: lowercase, dashes, short."""
    repo = re.search(r"github\.com/[^/\s]+/([A-Za-z0-9_.-]+)", text or "")
    base = repo.group(1).removesuffix(".git") if repo else " ".join((text or "work").split()[:4])
    name = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:30] or "work"
    return name if name[0].isalnum() else "w" + name


async def call(op: str, socket_path: str | None = None, timeout: float = 1900, **args) -> dict:
    """One request to the sandbox; raises SandboxError with its refusal."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(socket_path or SOCKET, limit=8_000_000),
                                                timeout=10)
    except (OSError, asyncio.TimeoutError) as e:
        raise SandboxError("The code sandbox isn't running (set it up with deploy/sandbox.sh).") from e
    try:
        writer.write(json.dumps({"op": op, **args}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    finally:
        writer.close()
    reply = json.loads(line or b'{"error": "no answer from the sandbox"}')
    if "error" in reply:
        raise SandboxError(reply["error"])
    return reply
