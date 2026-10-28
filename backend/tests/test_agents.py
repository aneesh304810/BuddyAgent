import asyncio
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from buddy.agents import AgentRegistry, AgentSpec, ApprovalNeeded, load_specs
from buddy.memory import Memory

BACKEND = Path(__file__).resolve().parents[1]


def make_server():
    srv = MCPServer("bank")
    sent = []

    @srv.tool()
    def balance(account: str) -> str:
        """Balance of an account."""
        return f"Balance of {account}: 1,200 (card 4111 1111 1111 1111)"

    @srv.tool()
    def pay_bill(card: str, amount: int) -> str:
        """Pay a card bill."""
        sent.append((card, amount))
        return f"Paid {amount}"

    return srv, sent


def run(coro):
    return asyncio.run(coro)


async def with_registry(fn, **spec_kw):
    srv, sent = make_server()
    mem = Memory(":memory:")
    reg = AgentRegistry(mem, [AgentSpec(name="bank", server=srv, approval_required=["pay_bill"], **spec_kw)])
    await reg.start()
    try:
        return await fn(reg, sent)
    finally:
        await reg.close()


def test_tools_exposed_and_results_masked():
    async def body(reg, sent):
        names = [t["name"] for t in reg.tool_definitions()]
        assert names == ["bank__balance", "bank__pay_bill"]
        assert "spoken yes" in reg.tools["bank__pay_bill"]["description"]
        out = await reg.call("bank__balance", {"account": "x"}, turn=1)
        assert "card ****1111" in out and "4111 1111" not in out
    run(with_registry(body))


def test_approval_needs_a_newer_user_turn():
    async def body(reg, sent):
        with pytest.raises(ApprovalNeeded) as e:
            await reg.call("bank__pay_bill", {"card": "HDFC", "amount": 500}, turn=3)
        action_id = next(iter(reg.pending))
        assert action_id in str(e.value) and sent == []
        assert "Not approved yet" in await reg.confirm(action_id, turn=3)  # same turn: refused
        assert sent == []
        assert await reg.confirm(action_id, turn=4) == "Paid 500"
        assert sent == [("HDFC", 500)]
        assert "No such pending" in await reg.confirm(action_id, turn=5)  # one use only
        statuses = [r["status"] for r in reg.audit_log()]
        assert statuses[:3] == ["ok", "approved", "approval_requested"]
    run(with_registry(body))


def test_kill_switch_and_call_limit():
    async def body(reg, sent):
        reg.set_enabled("bank", False)
        assert reg.tool_definitions() == []
        assert "switched off" in await reg.call("bank__balance", {"account": "x"}, turn=1)
        reg.set_enabled("bank", True)
        await reg.call("bank__balance", {"account": "x"}, turn=1)
        assert "daily limit" in await reg.call("bank__balance", {"account": "x"}, turn=1)
    run(with_registry(body, daily_call_limit=1))


def test_audit_args_are_masked():
    async def body(reg, sent):
        await reg.call("bank__balance", {"account": "50100234567812"}, turn=1)
        assert "50100234567812" not in reg.audit_log()[0]["args"]
    run(with_registry(body))


def test_worldclock_agent_over_stdio(tmp_path, monkeypatch):
    monkeypatch.chdir(BACKEND)
    specs = [AgentSpec(name="worldclock",
                       command=[sys.executable, "-m", "agents.worldclock.server"])]

    async def body():
        reg = AgentRegistry(Memory(":memory:"), specs)
        await reg.start()
        try:
            assert "worldclock__time_in" in reg.tools
            out = await reg.call("worldclock__days_until", {"day": "2099-01-01"}, turn=1)
            assert "days until 2099-01-01" in out
        finally:
            await reg.close()
    run(body())


def test_load_specs_validates_names(tmp_path):
    good = tmp_path / "a.yaml"
    good.write_text("agents:\n  - name: money\n    command: [x]\n")
    assert load_specs(good)[0].name == "money"
    bad = tmp_path / "b.yaml"
    bad.write_text("agents:\n  - name: bad__name\n    command: [x]\n")
    with pytest.raises(ValueError):
        load_specs(bad)
    assert load_specs(tmp_path / "missing.yaml") == []


# --- remote agents like Zapier: secret URL and token from settings, limits -------


def test_read_only_patterns_skip_approval():
    spec = AgentSpec(name="zapier", url_env="ZAPIER_MCP_URL", approval_required=["*"],
                     no_approval_for=["*_find_*", "*_lookup*", "*_search*"])
    assert spec.needs_approval("notion_create_page")
    assert spec.needs_approval("slack_send_channel_message")
    assert not spec.needs_approval("gmail_find_email")
    assert not spec.needs_approval("google_sheets_lookup_spreadsheet_row")


def test_unconfigured_remote_agent_is_skipped(monkeypatch):
    monkeypatch.delenv("ZAPIER_MCP_URL", raising=False)

    async def body():
        reg = AgentRegistry(Memory(":memory:"), [AgentSpec(name="zapier", url_env="ZAPIER_MCP_URL")])
        await reg.start()  # no error, just skipped
        assert reg.clients == {} and reg.status()[0]["connected"] is False
        await reg.close()

    run(body())


def test_monthly_limit_survives_restarts():
    async def body():
        srv, _ = make_server()
        mem = Memory(":memory:")
        reg = AgentRegistry(mem, [AgentSpec(name="bank", server=srv, monthly_call_limit=2)])
        await reg.start()
        for _ in range(2):
            assert "Balance" in await reg.call("bank__balance", {"account": "x"}, 1)
        await reg.close()
        reg2 = AgentRegistry(mem, [AgentSpec(name="bank", server=srv, monthly_call_limit=2)])
        await reg2.start()  # a restart: the in-memory daily counter is empty again
        out = await reg2.call("bank__balance", {"account": "x"}, 1)
        await reg2.close()
        return out

    assert "used its 2 calls" in run(body())


def test_remote_agent_over_http_with_bearer_token(monkeypatch):
    import socket
    import threading

    import uvicorn

    srv, _ = make_server()
    inner = srv.streamable_http_app()
    seen = []

    async def app(scope, receive, send):
        if scope["type"] == "http":
            seen.append(dict(scope["headers"]).get(b"authorization", b"").decode())
        await inner(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        import time as _t
        for _ in range(100):
            if server.started:
                break
            _t.sleep(0.05)
        monkeypatch.setenv("TEST_MCP_URL", f"http://127.0.0.1:{port}/mcp")
        monkeypatch.setenv("TEST_MCP_TOKEN", "s3cret")

        async def body():
            reg = AgentRegistry(Memory(":memory:"), [AgentSpec(
                name="zapier", url_env="TEST_MCP_URL", token_env="TEST_MCP_TOKEN",
                approval_required=["*"], no_approval_for=["balance"],
            )])
            await reg.start()
            try:
                assert "zapier__balance" in reg.tools
                out = await reg.call("zapier__balance", {"account": "main"}, 1)
                with pytest.raises(ApprovalNeeded):
                    await reg.call("zapier__pay_bill", {"card": "x", "amount": 5}, 1)
                return out
            finally:
                await reg.close()

        out = run(body())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert "Balance of main" in out and "4111 1111" not in out  # results masked
    assert seen and all(h == "Bearer s3cret" for h in seen)
