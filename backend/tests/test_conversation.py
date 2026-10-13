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
