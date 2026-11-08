"""The request trace: every call in an Excel file, with its cost split and safe text cells."""

import io
from datetime import datetime, timedelta, timezone

from openpyxl import load_workbook

from buddy import trace
from buddy.memory import Memory


def test_the_trace_lists_every_call_with_its_cost_parts():
    m = Memory(":memory:")
    now = datetime.now(timezone.utc)
    m.db.execute("INSERT INTO model_calls (at, provider, model, purpose, input_tokens, output_tokens, cache_read_tokens, "
                 "cache_write_tokens, cost_usd, ok, stop, request, reply) VALUES (?, 'claude', 'claude-sonnet-5-5', "
                 "'chat', 300, 200, 30000, 4000, 0.0226, 1, 'end_turn', '=HYPERLINK(\"x\")', 'Hi')",
                 ((now - timedelta(minutes=5)).isoformat(),))
    data = trace.build(m, (now - timedelta(hours=1)).isoformat())
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Summary", "Calls", "Outside APIs"]
    row = [c.value for c in wb["Calls"][2]]
    assert row[2] == "claude-sonnet-5-5" and row[11] == 0.0226
    assert row[14] > row[13]  # writing 4,000 tokens to the cache costs more than reading 30,000
    assert row[20].startswith(" =")  # text that looks like a formula stays text
    assert any("What Claude's cost is made of" == (r[0].value or "") for r in wb["Summary"].iter_rows())
