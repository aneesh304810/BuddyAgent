"""Agent registry: Buddy's specialist agents, each an MCP server.

Buddy (the orchestrator) connects to every enabled agent, offers their tools to
Claude as `<agent>__<tool>`, and enforces the safety rules on every call:

- only agents you enabled are reachable, and Claude can pause an agent but
  never re-enable one (only you can, through the admin API);
- tools listed as needing approval do nothing until you have said "yes";
- each agent has a daily call limit;
- every call is written to the audit log with its arguments masked;
- results are masked before Claude sees them.

Agents are declared in agents.yaml (see agents.example.yaml).
"""

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .memory import Memory
from .privacy import mask

log = logging.getLogger(__name__)

SEP = "__"  # Claude tool name = agent + SEP + tool
APPROVAL_TTL_SECONDS = 600


@dataclass
class AgentSpec:
    name: str
    description: str = ""
    # Exactly one of: command (stdio subprocess), url (Streamable HTTP), server (in-process, tests).
    command: list[str] | None = None
    url: str | None = None
    # Secret URLs and tokens (e.g. Zapier's) live in /etc/buddy/buddy.env, not in agents.yaml:
    # name the environment variables here instead.
    url_env: str | None = None
    token_env: str | None = None   # sent as "Authorization: Bearer <token>"
    server: Any = None
    env: dict[str, str] = field(default_factory=dict)
    # Settings passed through from /etc/buddy/buddy.env to a command agent (by name,
    # so secrets never sit in agents.yaml). Agents otherwise get a minimal environment.
    env_from: list[str] = field(default_factory=list)
    enabled: bool = True
    approval_required: list[str] = field(default_factory=list)  # tool names, or ["*"]
    # Tool-name patterns that never need approval, e.g. "*_find_*" for read-only lookups.
    no_approval_for: list[str] = field(default_factory=list)
    daily_call_limit: int = 200
    # Calls per 30 days (counted from the audit log, so restarts don't reset it). None = no limit.
    monthly_call_limit: int | None = None

    def needs_approval(self, tool: str) -> bool:
        if any(fnmatchcase(tool, pattern) for pattern in self.no_approval_for):
            return False
        return "*" in self.approval_required or tool in self.approval_required

    def endpoint(self) -> str | None:
        if self.url_env:
            return os.environ.get(self.url_env, "").strip() or None
        return self.url


@dataclass
class PendingAction:
    id: str
    agent: str
    tool: str
    args: dict
    created_turn: int
    created_at: float


class ApprovalNeeded(Exception):
    pass


def load_specs(path: Path) -> list[AgentSpec]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    specs = []
    for raw in data.get("agents", []):
        if "__" in raw["name"] or not raw["name"].replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Bad agent name {raw['name']!r}: letters, digits, - and single _ only")
        specs.append(AgentSpec(**raw))
    return specs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRegistry:
    def __init__(self, memory: Memory, specs: list[AgentSpec]):
        self.memory = memory
        self.specs = {s.name: s for s in specs}
        self.clients: dict[str, Client] = {}
        self.tools: dict[str, dict] = {}  # Claude tool name -> Claude tool definition
        self.pending: dict[str, PendingAction] = {}
        self._calls: dict[str, list[float]] = {}
        self._stack = AsyncExitStack()
        self.memory.db.execute(
            "CREATE TABLE IF NOT EXISTS agent_state (name TEXT PRIMARY KEY, enabled INTEGER NOT NULL)"
        )

    # --- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        for spec in self.specs.values():
            if not self.is_enabled(spec.name):
                continue
            if spec.url_env and not spec.endpoint():
                log.info("agent %s not set up yet (%s is empty); skipping", spec.name, spec.url_env)
                continue
            try:
                await self._connect(spec)
            except Exception:
                log.exception("agent %s failed to start; continuing without it", spec.name)

    async def close(self) -> None:
        await self._stack.aclose()

    async def _connect(self, spec: AgentSpec) -> None:
        if spec.server is not None:
            target = spec.server
        elif spec.command:
            env = {**{k: os.environ[k] for k in spec.env_from if k in os.environ}, **spec.env}
            target = StdioServerParameters(command=spec.command[0], args=spec.command[1:], env=env or None)
        elif spec.endpoint():
            token = os.environ.get(spec.token_env, "").strip() if spec.token_env else ""
            if token:
                http = await self._stack.enter_async_context(
                    create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
                )
                target = streamable_http_client(spec.endpoint(), http_client=http)
            else:
                target = spec.endpoint()
        elif spec.url_env:
            raise ValueError(f"agent {spec.name}: set {spec.url_env} in the server's settings file")
        else:
            raise ValueError(f"agent {spec.name} has no command, url or server")
        client = await self._stack.enter_async_context(Client(target))
        listing = await client.list_tools()
        self.clients[spec.name] = client
        for tool in listing.tools:
            name = f"{spec.name}{SEP}{tool.name}"
            if len(name) > 64:
                log.warning("skipping tool %s: name longer than 64 characters", name)
                continue
            note = " Needs the user's spoken yes first." if spec.needs_approval(tool.name) else ""
            self.tools[name] = {
                "name": name,
                "description": f"[{spec.name} agent] {tool.description or ''}{note}".strip(),
                "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            }
        log.info("agent %s connected with %d tools", spec.name, len(listing.tools))

    # --- enable / kill switch ---------------------------------------------
    def is_enabled(self, name: str) -> bool:
        row = self.memory.db.execute(
            "SELECT enabled FROM agent_state WHERE name = ?", (name,)
        ).fetchone()
        return bool(row["enabled"]) if row else self.specs[name].enabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self.specs:
            raise KeyError(name)
        self.memory.db.execute(
            "INSERT INTO agent_state (name, enabled) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled",
            (name, int(enabled)),
        )
        self.memory.db.commit()
        self.audit(name, "-", {}, "enabled" if enabled else "disabled")

    def status(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "enabled": self.is_enabled(s.name),
                "connected": s.name in self.clients,
                "tools": sorted(t for t in self.tools if t.startswith(s.name + SEP)),
                "approval_required": s.approval_required,
                "calls_last_24h": len(self._recent_calls(s.name)),
                "daily_call_limit": s.daily_call_limit,
                "calls_last_30_days": self._calls_last_30_days(s.name),
                "monthly_call_limit": s.monthly_call_limit,
            }
            for s in self.specs.values()
        ]

    # --- tool definitions for Claude -------------------------------------
    def tool_definitions(self) -> list[dict]:
        """Tools of enabled, connected agents, sorted so the prompt prefix stays cacheable."""
        return [
            self.tools[name]
            for name in sorted(self.tools)
            if self.is_enabled(name.split(SEP, 1)[0])
        ]

    # --- calling ---------------------------------------------------------
    def _recent_calls(self, agent: str) -> list[float]:
        cutoff = time.time() - 86400
        calls = [t for t in self._calls.get(agent, []) if t > cutoff]
        self._calls[agent] = calls
        return calls

    def _calls_last_30_days(self, agent: str) -> int:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        row = self.memory.db.execute(
            "SELECT COUNT(*) AS n FROM audit WHERE agent = ? AND status IN ('ok', 'error') AND ts >= ?",
            (agent, since),
        ).fetchone()
        return int(row["n"])

    async def call(self, claude_name: str, args: dict, turn: int) -> str:
        """Run an agent tool for Claude. Raises ApprovalNeeded for gated tools."""
        agent, _, tool = claude_name.partition(SEP)
        spec = self.specs.get(agent)
        if spec is None or claude_name not in self.tools:
            raise KeyError(f"No such agent tool {claude_name}")
        if not self.is_enabled(agent) or agent not in self.clients:
            self.audit(agent, tool, args, "blocked", "agent disabled")
            return f"The {agent} agent is switched off."
        if spec.needs_approval(tool):
            action = PendingAction(
                id=secrets.token_hex(3), agent=agent, tool=tool, args=args,
                created_turn=turn, created_at=time.time(),
            )
            self.pending[action.id] = action
            self.audit(agent, tool, args, "approval_requested", action.id)
            raise ApprovalNeeded(
                f"Approval required (action {action.id}). Tell the user exactly what will "
                f"happen and ask for a yes. Only after they say yes in a new message, call "
                f"confirm_action with id {action.id}."
            )
        return await self._execute(spec, tool, args)

    async def confirm(self, action_id: str, turn: int) -> str:
        """Execute a pending action, but only if the user has spoken since it was requested."""
        action = self.pending.get(action_id)
        if action is None:
            return "No such pending action. Request it again."
        if time.time() - action.created_at > APPROVAL_TTL_SECONDS:
            del self.pending[action_id]
            return "That approval expired. Request it again and ask the user once more."
        if turn <= action.created_turn:
            return (
                "Not approved yet: the user has not replied since you asked. "
                "Wait for their yes in their next message."
            )
        del self.pending[action_id]
        spec = self.specs[action.agent]
        if not self.is_enabled(spec.name):
            return f"The {spec.name} agent was switched off; nothing was done."
        self.audit(action.agent, action.tool, action.args, "approved", action_id)
        return await self._execute(spec, action.tool, action.args)

    async def _execute(self, spec: AgentSpec, tool: str, args: dict) -> str:
        if len(self._recent_calls(spec.name)) >= spec.daily_call_limit:
            self.audit(spec.name, tool, args, "blocked", "daily call limit reached")
            return f"The {spec.name} agent hit its daily limit of {spec.daily_call_limit} calls."
        if spec.monthly_call_limit is not None and self._calls_last_30_days(spec.name) >= spec.monthly_call_limit:
            self.audit(spec.name, tool, args, "blocked", "monthly call limit reached")
            return (
                f"The {spec.name} agent used its {spec.monthly_call_limit} calls for the last 30 days "
                "(monthly_call_limit in agents.yaml)."
            )
        self._calls.setdefault(spec.name, []).append(time.time())
        try:
            result = await asyncio.wait_for(
                self.clients[spec.name].call_tool(tool, args), timeout=60
            )
        except Exception as e:
            self.audit(spec.name, tool, args, "error", repr(e))
            return f"The {spec.name} agent failed: {type(e).__name__}."
        text = "\n".join(getattr(b, "text", "") for b in result.content if getattr(b, "text", ""))
        if not text and result.structured_content is not None:
            text = json.dumps(result.structured_content)
        status = "error" if result.is_error else "ok"
        self.audit(spec.name, tool, args, status)
        return mask(text) or "(no output)"

    # --- audit -----------------------------------------------------------
    def audit(self, agent: str, tool: str, args: dict, status: str, detail: str = "") -> None:
        self.memory.db.execute(
            "INSERT INTO audit (ts, agent, tool, args, status, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), agent, tool, mask(json.dumps(args, ensure_ascii=False)), status, detail[:500]),
        )
        self.memory.db.commit()

    def audit_log(self, limit: int = 100) -> list[dict]:
        rows = self.memory.db.execute(
            "SELECT ts, agent, tool, args, status, detail FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]
