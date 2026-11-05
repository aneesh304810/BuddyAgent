"""Seat helper: reads the airline's seat map, picks well, taps only after a yes, never confirms or pays."""

import asyncio

import pytest

from buddy import seats
from buddy.memory import Memory
from buddy.tools import ToolError


def seat_map():
    """Rows 30-45 of a 3-3-3 cabin (A B C | D E F | G H K), as the phone's form helper sends it."""
    nodes, n = [{"kind": "text", "t": "Choose your seat"}], 0
    xs = {"A": 100, "B": 150, "C": 200, "D": 300, "E": 350, "F": 400, "G": 500, "H": 550, "K": 600}
    for row in range(30, 46):
        for letter, x in xs.items():
            n += 1
            taken = (row + ord(letter)) % 3 == 0
            price = "USD 45" if row == 30 else "Free"
            label = f"Seat {row}{letter}, {'Unavailable' if taken else 'Available'}, {price}"
            nodes.append({"kind": "button", "i": f"f{n}", "t": label, "x": x, "off": taken})
    nodes.append({"kind": "button", "i": "f999", "t": "Continue to payment"})
    return nodes


class Phone:
    def __init__(self, nodes):
        self.nodes, self.sent = nodes, []

    async def __call__(self, msg):
        self.sent.append(msg)
        if msg["action"] == "fill":
            return {"done": msg["fills"]}
        return {"nodes": self.nodes, "can_scroll": False}


class Thrift:
    def __init__(self):
        self.asked = []

    def ask_action(self, question, go):
        self.asked.append((question, go))
        return f"ASKED: {question}"


def test_seats_window_aisle_and_price_come_from_the_map():
    found = {s["seat"]: s for s in seats.seats_on_screen(seat_map())}
    assert found["31A"]["kind"] == "window" and found["31K"]["kind"] == "window"
    assert found["31C"]["kind"] == "aisle" and found["31D"]["kind"] == "aisle" and found["31E"]["kind"] == "middle"
    assert found["30A"]["price"] == 45 and found["30A"]["currency"] == "USD" and found["31A"]["price"] == 0
    assert "f999" not in {s["field"] for s in found.values()}


def test_picks_a_free_seat_of_the_kind_they_like_not_a_paid_or_back_row():
    picks = seats.rank(seats.seats_on_screen(seat_map()), like="window")
    top = picks[0]["seats"][0]
    s = {x["seat"]: x for x in seats.seats_on_screen(seat_map())}[top]
    assert s["kind"] == "window" and s["price"] == 0 and not s["taken"] and s["row"] < 43
    pair = seats.rank(seats.seats_on_screen(seat_map()), like="window", together=2)[0]["seats"]
    assert len(pair) == 2 and pair[0][:-1] == pair[1][:-1]


def test_selecting_asks_first_then_taps_only_the_seat():
    phone, thrift, state = Phone(seat_map()), Thrift(), {}
    out = asyncio.run(seats.run_seat_tool(phone, thrift, state, Memory(":memory:"), {"action": "read", "like": "aisle"}))
    assert "Best picks" in out and "They like: aisle" in out
    open_seat = next(s for s in state["seats"].values() if not s["taken"] and s["row"] == 33)
    asked = asyncio.run(seats.run_seat_tool(phone, thrift, state, None, {"action": "choose", "seats": [open_seat["seat"]]}))
    assert asked.startswith("ASKED: Select " + open_seat["seat"]) and phone.sent[-1]["action"] == "read"  # no tap yet
    done = asyncio.run(thrift.asked[0][1]())
    assert phone.sent[-1]["fills"] == [{"field": open_seat["field"], "tap": True}]
    assert "tap Continue or Confirm yourself" in done


def test_taken_or_unknown_seats_are_refused():
    phone, state = Phone(seat_map()), {}
    asyncio.run(seats.run_seat_tool(phone, None, state, Memory(":memory:"), {"action": "read"}))
    taken = next(s for s in state["seats"].values() if s["taken"])
    with pytest.raises(ToolError, match="taken"):
        asyncio.run(seats.run_seat_tool(phone, None, state, None, {"action": "choose", "seats": [taken["seat"]]}))
    with pytest.raises(ToolError, match="isn't on the screen"):
        asyncio.run(seats.run_seat_tool(phone, None, state, None, {"action": "choose", "seats": ["99Z"]}))


def test_a_map_the_phone_cant_read_is_said_plainly():
    out = asyncio.run(seats.run_seat_tool(Phone([{"kind": "text", "t": "Seat map"}]), None, {}, Memory(":memory:"),
                                          {"action": "read"}))
    assert "No seats I can read" in out
