from types import SimpleNamespace

from buddy import usage
from buddy.memory import Memory


def test_cost_and_month_to_date():
    mem = Memory(":memory:")
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000,
                        cache_read_input_tokens=2_000_000, cache_creation_input_tokens=0)
    # 1M in at $5 + 2M cached at $0.50 + 0.1M out at $25 = 5 + 1 + 2.5
    assert abs(usage.cost_usd("claude-opus-5", u) - 8.5) < 1e-9
    usage.record(mem, "claude-opus-5", u)
    usage.record(mem, "claude-opus-5", u)
    assert abs(usage.month_to_date(mem) - 17.0) < 1e-9
    assert usage.summary(mem)[0]["input_tokens"] == 2_000_000


def test_haiku_and_web_search_prices():
    from types import SimpleNamespace
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000,
                        server_tool_use=SimpleNamespace(web_search_requests=10))
    assert abs(usage.cost_usd("claude-haiku-5-5", u) - (0.10 + 0.50 + 0.10)) < 1e-9
    assert abs(usage.cost_usd("claude-sonnet-5-5", u) - (2 + 10 + 0.10)) < 1e-9


def test_how_a_reply_was_answered_names_the_ai_and_the_tools():
    mem = Memory(":memory:")
    assert usage.how(mem, "") == "" and usage.how(mem, "t0") == ""  # nothing logged, nothing claimed
    token = usage.current_turn.set("t1")
    usage.log_call(mem, provider="claude", model="claude-haiku-5-5", purpose="chat", tools="calendar_events, web_search")
    usage.current_turn.set("t2")
    usage.log_call(mem, provider="claude", model="claude-haiku-5-5", purpose="chat")
    usage.log_call(mem, provider="cerebras", model="llama", purpose="summary")
    usage.current_turn.set("t3")
    usage.log_call(mem, provider="cerebras", model="llama", purpose="translate")
    usage.current_turn.set("t4")
    usage.log_call(mem, provider="flightaware", model="flights", purpose="flight status")
    usage.current_turn.reset(token)
    assert usage.how(mem, "t1") == "Answered by Claude · used calendar events, web search"
    assert usage.how(mem, "t2") == "Answered by Claude, with a free model (cerebras) for a public part"
    assert usage.how(mem, "t3") == "Answered by a free model (cerebras); nothing personal was sent to it"
    assert usage.how(mem, "t4") == ""  # an outside service is not an AI answering
