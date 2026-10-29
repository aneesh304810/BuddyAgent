"""Agent builder: Buddy grows new skills when a new kind of problem comes up.

Two ways:
  - connect an existing MCP server by its URL (Notion, a company tool, any service
    that offers one), with every change gated behind the user's yes
  - build a new agent: a background job researches the service's API, writes a
    small MCP server in Python, checks it (an import allowlist and banned calls,
    then a second model's safety review), and starts it once to list its tools.
    The user hears what it can do and which websites it talks to, and nothing runs
    until they say yes.

Built agents run as their own process with only the settings they declared, ask
before every tool unless the user later relaxes that, and can be paused or removed
by voice. Code lives in the data folder, never in the app's own code.
"""

import ast
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .agents import AgentRegistry, AgentSpec
from .config import claude_client, settings
from .jobs import JobContext, JobFailed, JobResult
from .memory import Memory
from .privacy import mask
from .tools import ToolError

log = logging.getLogger(__name__)
client = claude_client()

BUILD_COST = (1.20, "about a dollar")
NAME = re.compile(r"^[a-z][a-z0-9]{1,20}$")
ALLOWED_IMPORTS = {
    "mcp", "httpx", "json", "re", "datetime", "time", "math", "statistics", "decimal", "fractions",
    "typing", "dataclasses", "collections", "itertools", "functools", "enum", "html", "csv", "io",
    "base64", "hashlib", "hmac", "uuid", "random", "string", "textwrap", "zoneinfo", "calendar",
    "urllib", "xml", "os", "logging",
}
BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "globals", "vars",
                "getattr", "setattr", "delattr", "locals", "memoryview", "type", "super"}
OS_ALLOWED = {"environ", "getenv"}
# Attribute names that reach files, processes or the interpreter, on any object
# (e.g. io.open, os.path.os.system, pathlib's read_text).
BANNED_ATTRIBUTES = {
    "open", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "remove", "rmdir", "rmtree",
    "rename", "mkdir", "makedirs", "listdir", "scandir", "walk", "glob", "chmod", "chown", "symlink",
    "system", "popen", "fork", "kill", "exec", "execv", "execve", "spawn", "startfile", "os", "sys",
    "subprocess", "builtins", "modules", "import_module", "loader", "f_globals", "f_locals", "gi_frame",
    "tb_frame", "mount", "transport", "file",
}
MCP_IMPORTS = {"mcp.server.mcpserver": {"MCPServer"}, "mcp.types": None}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS built_agents (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- built | remote
    purpose TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    tools TEXT NOT NULL DEFAULT '[]',     -- [{name, does, changes_things}]
    hosts TEXT NOT NULL DEFAULT '[]',
    settings TEXT NOT NULL DEFAULT '[]',  -- env names it needs (API keys...)
    url TEXT NOT NULL DEFAULT '',
    token_setting TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,            -- draft | active | removed
    created_at TEXT NOT NULL
);
"""

BUILD_PROMPT = """\
Write a new specialist agent for {user}'s personal assistant, as one Python file that is \
an MCP server. Purpose: {purpose}
Details from {user}: {details}

First research the real service or API you'll use (web_search, then read its official \
docs with web_fetch): endpoints, authentication, limits. Prefer official APIs that need \
no key or a free key.

The file must follow this shape exactly:

    import json
    import os
    import httpx
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("{name}")

    @server.tool()
    def find_something(query: str, limit: int = 5) -> str:
        '''What it does, in one or two sentences the assistant will read.'''
        ...
        return json.dumps(result)

    if __name__ == "__main__":
        server.run()

Rules (the code is checked automatically and rejected if it breaks one):
- Imports only from: {allowed}.
- No open(), eval, exec, subprocess, sockets, file access, or getattr tricks. os only for \
os.environ / os.getenv.
- Network only with httpx, only to the hosts you list, always with a timeout of 30 seconds or less.
- API keys come from environment variables named BUDDY_{upper}_<SOMETHING>; list them in \
"settings". Never hard-code secrets.
- Name tools that only read find_, get_, search_ or list_; anything that creates, sends, \
books, buys or changes something gets a clear verb (create_, send_, book_...).
- Return short JSON strings; catch errors and return a plain "Error: ..." message.
- Keep it small: 2 to 6 tools, under 250 lines.

Your final message must contain the code in one ```python block, then one ```json block:
{{"summary": "one sentence a friend would say about what the agent can do",
  "tools": [{{"name": "...", "does": "...", "changes_things": false}}],
  "hosts": ["api.example.com"], "settings": ["BUDDY_{upper}_API_KEY"]}}"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "safe": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["safe", "problems"],
    "additionalProperties": False,
}


def check_code(code: str, hosts: list[str]) -> list[str]:
    """Static checks a built agent must pass. Returns the problems (empty = passed)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS or alias.name.split(".")[0] == "mcp":
                    problems.append(f"import of {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if node.level or module.split(".")[0] not in ALLOWED_IMPORTS:
                problems.append(f"import from {module or '.'} is not allowed")
            elif module.split(".")[0] == "mcp":
                allowed = MCP_IMPORTS.get(module, set())
                if module not in MCP_IMPORTS or (allowed is not None and not names <= allowed):
                    problems.append(f"from {module} import {', '.join(sorted(names))} is not allowed")
            if any(n.startswith("_") or n == "*" for n in names):
                problems.append(f"importing private or all names from {module} is not allowed")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BANNED_CALLS:
                problems.append(f"call to {fn.id}() is not allowed")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr not in OS_ALLOWED:
                problems.append(f"os.{node.attr} is not allowed")
            elif node.attr in BANNED_ATTRIBUTES:
                problems.append(f".{node.attr} is not allowed")
            if node.attr.startswith("_"):
                problems.append(f"private attribute {node.attr} is not allowed")
        elif isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id != "__name__":
                problems.append(f"{node.id} is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for host in re.findall(r"https?://([^/\s\"'{}:]+)", node.value):
                if not any(host == h or host.endswith("." + h) for h in hosts):
                    problems.append(f"talks to {host}, which isn't in its list of hosts")
            if re.search(r"\bhttp://", node.value):
                problems.append("plain http:// links are not allowed")
    if "MCPServer(" not in code or "server.run()" not in code:
        problems.append("it isn't an MCP server in the expected shape")
    return sorted(set(problems))


class AgentBuilder:
    def __init__(self, memory: Memory, registry: AgentRegistry | None = None, folder: Path | None = None):
        self.memory = memory
        self.registry = registry
        self.folder = folder or (Path(settings.db_path).resolve().parent / "custom_agents")
        memory.db.executescript(SCHEMA_SQL)

    def path(self, name: str) -> Path:
        return self.folder / f"{name}.py"

    def get(self, name: str) -> dict | None:
        row = self.memory.db.execute("SELECT * FROM built_agents WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        out = dict(row)
        for key in ("tools", "hosts", "settings"):
            out[key] = json.loads(out[key])
        return out

    def all(self) -> list[dict]:
        names = [r["name"] for r in self.memory.db.execute(
            "SELECT name FROM built_agents WHERE status != 'removed' ORDER BY created_at")]
        return [self.get(n) for n in names]

    def _taken(self, name: str) -> bool:
        existing = self.get(name)
        in_registry = self.registry is not None and name in self.registry.specs
        return in_registry or (existing is not None and existing["status"] != "removed")

    def spec(self, agent: dict) -> AgentSpec:
        read_only = [t["name"] for t in agent["tools"] if not t.get("changes_things")]
        if agent["kind"] == "remote":
            return AgentSpec(
                name=agent["name"], description=agent["purpose"], url=agent["url"],
                token_env=agent["token_setting"] or None, approval_required=["*"],
                no_approval_for=["*find*", "*search*", "*get*", "*list*", "*lookup*", "*retrieve*"],
                daily_call_limit=100,
            )
        home = self.folder / f"{agent['name']}_home"  # its own empty folder, not Buddy's data
        return AgentSpec(
            name=agent["name"], description=agent["summary"] or agent["purpose"],
            command=[sys.executable, "-I", str(self.path(agent["name"]))],
            env_from=agent["settings"], env={"HOME": str(home)}, cwd=str(home),
            approval_required=["*"], no_approval_for=read_only, daily_call_limit=100,
        )

    def active_specs(self) -> list[AgentSpec]:
        return [self.spec(a) for a in self.all() if a["status"] == "active"]

    async def activate(self, name: str) -> str:
        agent = self.get(name)
        if not agent or agent["status"] == "removed":
            raise ToolError(f"No built agent called {name}.")
        spec = self.spec(agent)
        if spec.cwd:
            Path(spec.cwd).mkdir(parents=True, exist_ok=True)
        if self.registry is not None:
            try:
                await self.registry.add(spec)  # same task: the MCP client's scopes must stay in it
            except Exception as e:
                return f"The {name} agent didn't start ({type(e).__name__}), so it stays off."
        self.memory.db.execute("UPDATE built_agents SET status = 'active' WHERE name = ?", (name,))
        self.memory.db.commit()
        missing = [s for s in agent["settings"] if s not in os.environ]
        note = (f" It needs {', '.join(missing)} in the server settings: run "
                f"sudo bash /opt/buddy/deploy/set.sh {missing[0]}." if missing else "")
        return f"The {name} agent is on.{note}"

    def remove(self, name: str) -> bool:
        if not self.get(name):
            return False
        self.memory.db.execute("UPDATE built_agents SET status = 'removed' WHERE name = ?", (name,))
        self.memory.db.commit()
        if self.registry is not None and name in self.registry.specs:
            self.registry.set_enabled(name, False)
        return True

    def add_remote(self, name: str, url: str, purpose: str, token_setting: str = "") -> dict:
        if not url.startswith("https://"):
            raise ToolError("The MCP server's address must start with https://.")
        self.memory.db.execute(
            "INSERT OR REPLACE INTO built_agents (name, kind, purpose, summary, url, token_setting, status, created_at) "
            "VALUES (?, 'remote', ?, ?, ?, ?, 'draft', ?)",
            (name, purpose, purpose, url, token_setting, datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        return self.get(name)

    # --- the build job -----------------------------------------------------
    async def build(self, job: dict, ctx: JobContext) -> JobResult:
        from .agent import fallback_kwargs

        params = job["params"]
        name = params["name"]
        prompt = BUILD_PROMPT.format(user=settings.user_name, purpose=params["purpose"],
                                     details=params.get("details") or "none", name=name, upper=name.upper(),
                                     allowed=", ".join(sorted(ALLOWED_IMPORTS)))
        messages = [{"role": "user", "content": prompt}]
        code, meta, problems = "", {}, []
        for attempt in range(2):
            response = None
            for _ in range(12):
                async with client.beta.messages.stream(
                    model=settings.model, max_tokens=32_000, messages=messages,
                    tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
                           {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8}],
                    output_config={"effort": "high"}, **fallback_kwargs(settings.model),
                ) as stream:
                    response = await stream.get_final_message()
                ctx.record(response)
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "pause_turn":
                    break
            text = "\n".join(b.text for b in response.content if b.type == "text")
            code_match = re.search(r"```python\n(.*?)```", text, re.S)
            meta_match = re.search(r"```json\n(.*?)```", text, re.S)
            if not code_match or not meta_match:
                raise JobFailed("The builder didn't return code and a description.")
            code = code_match.group(1)
            try:
                meta = json.loads(meta_match.group(1))
            except json.JSONDecodeError as e:
                raise JobFailed("The builder's description wasn't readable.") from e
            problems = check_code(code, meta.get("hosts", [])) + await self._review(ctx, code, meta)
            if not problems:
                break
            messages.append({"role": "user", "content": "The checks rejected it:\n- " + "\n- ".join(problems)
                             + "\nFix every problem and send the whole file and the JSON again."})
        if problems:
            raise JobFailed("The agent didn't pass the safety checks: " + "; ".join(problems[:5]))

        self.folder.mkdir(parents=True, exist_ok=True)
        self.path(name).write_text(code)
        tools = meta.get("tools", [])  # it first runs when the user switches it on
        self.memory.db.execute(
            "INSERT OR REPLACE INTO built_agents (name, kind, purpose, summary, tools, hosts, settings, status, "
            "created_at) VALUES (?, 'built', ?, ?, ?, ?, ?, 'draft', ?)",
            (name, params["purpose"], meta.get("summary", ""), json.dumps(meta.get("tools", [])),
             json.dumps(meta.get("hosts", [])), json.dumps(meta.get("settings", [])),
             datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        hosts = ", ".join(meta.get("hosts", [])) or "no websites"
        report = (f"# The {name} agent\n\n{meta.get('summary', '')}\n\n## Tools\n"
                  + "\n".join(f"- **{t['name']}**: {t['does']}" + (" _(asks you first)_" if t.get("changes_things") else "")
                              for t in meta.get("tools", []))
                  + f"\n\n## Talks to\n{hosts}\n\n## Needs\n"
                  + (", ".join(meta.get("settings", [])) or "nothing")
                  + f"\n\n## Code\n```python\n{code}\n```")
        return JobResult(
            summary=f"Your {name} agent is built: {meta.get('summary', '')} It talks to {hosts}, "
                    f"and its {len(tools)} tools ask you before changing anything. It hasn't run yet.",
            report=report, findings=[t["does"] for t in meta.get("tools", [])], confidence="high")

    async def _review(self, ctx: JobContext, code: str, meta: dict) -> list[str]:
        from .agent import fallback_kwargs

        response = await client.beta.messages.create(
            model=settings.chat_model, max_tokens=2_000,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
            **fallback_kwargs(settings.chat_model),
            messages=[{"role": "user", "content": (
                "Review this generated agent before it runs on someone's personal server. Unsafe means: "
                "it reads or sends data it shouldn't, talks to hosts other than these, hides behaviour, "
                "could spend money or delete things without a clearly named tool, or could harm the server. "
                f"Declared hosts: {meta.get('hosts')}. Declared tools: {json.dumps(meta.get('tools'))}.\n\n"
                f"```python\n{code}\n```")}],
        )
        ctx.record(response)
        try:
            verdict = json.loads("".join(b.text for b in response.content if b.type == "text"))
        except json.JSONDecodeError:
            return ["the safety review couldn't be read"]
        return [] if verdict["safe"] else [f"review: {p}" for p in verdict["problems"]] or ["review: unsafe"]


BUILDER_TOOLS = [
    {
        "name": "build_agent",
        "description": "When the user needs a capability none of your tools or agents has (a service, an API, a "
        "kind of lookup), build a new agent for it: researched, written, safety-checked and tested in the "
        "background (about a dollar, asks first). The user approves turning it on when it's ready. "
        "name: short lowercase word, e.g. 'weather', 'strava', 'flights'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "purpose": {"type": "string", "description": "What it should do, in one sentence"},
                "details": {"type": "string", "description": "Service to use, accounts, what matters to the user"},
            },
            "required": ["name", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "connect_mcp_server",
        "description": "Connect an existing MCP server the user gives you the address of (https://...), as a new "
        "agent. If it needs a token, token_setting names the server setting holding it (the user adds it with "
        "set.sh). Asks first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "url": {"type": "string"},
                "purpose": {"type": "string"},
                "token_setting": {"type": "string"},
            },
            "required": ["name", "url", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "name": "built_agents",
        "description": "Agents Buddy has built or connected, with what they do, the websites they talk to and "
        "whether they're on. activate turns a ready one on (asks first); remove deletes one.",
        "input_schema": {
            "type": "object",
            "properties": {"activate": {"type": "string"}, "remove": {"type": "string"}},
            "additionalProperties": False,
        },
    },
]
BUILDER_TOOL_NAMES = {t["name"] for t in BUILDER_TOOLS}


async def run_builder_tool(builder: AgentBuilder, runner, name: str, args: dict, thrift=None) -> str:
    def ask(question: str, run):
        if thrift is None:
            raise ToolError("Approval isn't available.")
        return thrift.ask_action(question, run)

    if name == "build_agent":
        agent_name = str(args.get("name", "")).strip().lower()
        if not NAME.match(agent_name):
            raise ToolError("The name must be one short lowercase word, like weather or strava.")
        if builder._taken(agent_name):
            raise ToolError(f"There's already an agent called {agent_name}.")
        params = {"name": agent_name, "purpose": mask(args["purpose"]), "details": mask(args.get("details", ""))}

        async def start() -> str:
            runner.submit("build_agent", f"{agent_name} agent", params)
            return "Okay, I'm building it. I'll tell you when it's ready to switch on."
        return ask(f"Building a {agent_name} agent to {params['purpose'].rstrip('.').lower()} costs "
                   f"{BUILD_COST[1]}. Shall I build it?", start)
    if name == "connect_mcp_server":
        agent_name = str(args.get("name", "")).strip().lower()
        if not NAME.match(agent_name) or builder._taken(agent_name):
            raise ToolError("Pick a new short lowercase name for it.")
        agent = builder.add_remote(agent_name, args["url"].strip(), args["purpose"], args.get("token_setting", ""))

        async def connect() -> str:
            return await builder.activate(agent["name"])
        return ask(f"Connect {agent['url']} as your {agent_name} agent? Anything that changes things will "
                   "still ask you first.", connect)
    if name == "built_agents":
        if args.get("remove"):
            return "Removed." if builder.remove(args["remove"]) else "No such agent."
        if args.get("activate"):
            agent = builder.get(args["activate"])
            if not agent or agent["status"] == "removed":
                raise ToolError("No such agent.")

            async def on() -> str:
                return await builder.activate(agent["name"])
            hosts = ", ".join(agent["hosts"]) or agent.get("url") or "no websites"
            return ask(f"Turn on the {agent['name']} agent? It talks to {hosts}.", on)
        agents = [{k: a[k] for k in ("name", "kind", "status", "summary", "tools", "hosts", "settings", "url")}
                  for a in builder.all()]
        return json.dumps(agents) if agents else "No built agents yet."
    raise ToolError(f"Unknown tool {name}")
