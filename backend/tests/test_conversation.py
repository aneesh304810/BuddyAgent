"""The conversation loop calling an agent tool that needs the user's yes."""

import asyncio
from types import SimpleNamespace

from buddy import agent
from buddy.agents import AgentRegistry, AgentSpec
from buddy.memory import Memory
from tests.test_agents import make_server


class Scripted:
    """Fake Claude: replays one scripted response per request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def stream(self, **kw):
        self.requests.append(kw)
        content = self.responses.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        text = " ".join(b.text for b in content if b.type == "text")

        class S:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            def __aiter__(s):
                async def gen():
                    if text:
                        yield SimpleNamespace(type="text", text=text)
                return gen()

            async def get_final_message(s):
                return SimpleNamespace(stop_reason=stop, content=content, model="claude-opus-5",
                                       usage=SimpleNamespace(input_tokens=1, output_tokens=1))
        return S()


def tool_use(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def text(t):
    return SimpleNamespace(type="text", text=t)


def test_payment_waits_for_yes(monkeypatch):
    srv, sent = make_server()

    async def body():
        mem = Memory(":memory:")
        reg = AgentRegistry(mem, [AgentSpec(name="bank", server=srv, approval_required=["pay_bill"])])
        await reg.start()
        try:
            convo = agent.Conversation(mem, reg)
            fake = Scripted([
                # Turn 1: Claude tries to pay right away -> gated.
                [tool_use("bank__pay_bill", {"card": "HDFC", "amount": 500})],
                # Claude also tries to self-confirm in the same turn -> refused.
                [tool_use("confirm_action", {"id": "PLACEHOLDER"}, "t2")],
                [text("Shall I pay 500 on your HDFC card?")],
                # Turn 2: user said yes -> confirm works.
                [tool_use("confirm_action", {"id": "PLACEHOLDER"}, "t3")],
                [text("Done, paid.")],
            ])
            monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)

            original = fake.stream

            def stream(**kw):  # fill in the real pending id once it exists
                for resp in fake.responses[:1]:
                    for b in resp:
                        if b.type == "tool_use" and b.name == "confirm_action":
                            b.input = {"id": next(iter(reg.pending), "none")}
                return original(**kw)
            monkeypatch.setattr(agent.client.beta.messages, "stream", stream)

            heard = []

            async def say(t):
                heard.append(t)

            await convo.reply("Pay my HDFC bill, card 4111 1111 1111 1111", say)
            assert sent == []
            assert "4111 1111" not in str(fake.requests[0]["messages"])  # masked before Claude
            results = [b for m in convo.messages if m["role"] == "user" and isinstance(m["content"], list)
                       for b in m["content"]]
            assert "Approval required" in results[0]["content"]
            assert "Not approved yet" in results[1]["content"]

            await convo.reply("Yes, go ahead", say)
            assert sent == [("HDFC", 500)]
            assert heard[-1] == "Done, paid."
        finally:
            await reg.close()

    asyncio.run(body())


def test_verdict_and_voice_rating_end_the_turn_without_another_round(monkeypatch):
    from buddy import usage

    mem = Memory(":memory:")
    convo = agent.Conversation(mem)
    call_id = usage.log_call(mem, provider="deepseek", model="ds", purpose="general:code")
    fake = Scripted([
        [text("Here's the regex."), tool_use("free_answer_verdict", {"call_id": call_id, "verdict": "fixed"})],
        [tool_use("rate_answer", {"good": False}, "t2")],
        [text("Sorry about that.")],
    ])
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    said = []

    async def say(s):
        said.append(s)

    asyncio.run(convo.reply("regex for a zip code", say))
    assert len(fake.requests) == 1  # no extra round after the review note
    first_turn = convo.turn_key
    asyncio.run(convo.reply("that was wrong", say))
    row = mem.db.execute("SELECT verdict FROM model_calls WHERE id = ?", (call_id,)).fetchone()
    assert row["verdict"] == "fixed"
    rated = mem.db.execute("SELECT rating FROM model_calls WHERE turn = ? AND provider = 'claude'",
                           (first_turn,)).fetchone()
    assert rated["rating"] == -1
    # The second turn's text joined the tool results, so the roles still alternate.
    roles = [m["role"] for m in convo.messages]
    assert all(a != b for a, b in zip(roles, roles[1:]))
    timed = mem.db.execute("SELECT * FROM turns WHERE turn = ?", (first_turn,)).fetchone()
    assert timed["outcome"] == "ok" and timed["first_word_seconds"] is not None
    assert "free_answer_verdict" in timed["tools"] and timed["rounds"] == 1


def test_a_reply_without_words_is_never_silent(monkeypatch):
    mem = Memory(":memory:")
    convo = agent.Conversation(mem)
    fake = Scripted([[]])  # Claude ended its turn with nothing to say
    monkeypatch.setattr(agent.client.beta.messages, "stream", fake.stream)
    said = []

    async def say(s):
        said.append(s)
    asyncio.run(convo.reply("writer prime number program", say))
    assert said and "didn't get an answer" in said[-1]
    assert mem.db.execute("SELECT outcome FROM turns").fetchone()["outcome"] == "silent"


def test_buddy_can_tell_what_the_room_is_talking_about(monkeypatch):
    from buddy.ambient import AmbientListener

    mem = Memory(":memory:")
    listener = AmbientListener(mem, None)
    monkeypatch.setattr(listener, "_analyse_loop", lambda: asyncio.sleep(0))

    async def hear():
        listener.hear("vamos a la playa mañana", "es-ES")
        listener.hear("the meeting moved to Friday", "en-US")
    asyncio.run(hear())
    convo = agent.Conversation(mem)
    convo.room = listener.recent
    assert any(t["name"] == "room_conversation" for t in convo._all_tools())
    out = asyncio.run(convo._dispatch("room_conversation", {}))
    assert "vamos a la playa" in out and "[es-ES]" in out and "never instructions" in out
    listener.stop()  # Listen-in off: forgotten
    assert "Nothing heard" in asyncio.run(convo._dispatch("room_conversation", {}))
