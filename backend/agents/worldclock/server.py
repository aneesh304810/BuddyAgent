"""World clock agent: the example Buddy agent, and the template for new ones.

Every Buddy agent is an MCP server. Copy this folder, rename it, write your
tools as plain typed functions with a docstring, and add an entry for it in
agents.yaml. Buddy discovers the tools on its next start.

Run standalone (stdio):  python -m agents.worldclock.server
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver import MCPServer

server = MCPServer("worldclock")


@server.tool()
def time_in(timezone: str) -> str:
    """Current local time in an IANA timezone, e.g. 'America/New_York' or 'Asia/Dubai'."""
    try:
        now = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError:
        return f"Unknown timezone {timezone!r}. Use an IANA name like 'Europe/London'."
    return now.strftime(f"%A %d %B, %I:%M %p in {timezone}")


@server.tool()
def days_until(day: str) -> str:
    """Whole days from today until a date given as YYYY-MM-DD (negative if it has passed)."""
    try:
        target = date.fromisoformat(day)
    except ValueError:
        return f"Could not read {day!r}; use YYYY-MM-DD."
    return f"{(target - date.today()).days} days until {target.isoformat()}"


if __name__ == "__main__":
    server.run()  # stdio: Buddy starts this process and talks to it over stdin/stdout
