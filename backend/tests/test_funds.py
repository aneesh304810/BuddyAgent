"""Fund holdings from 13F filings: reading, units, quarter-on-quarter changes, who holds, one bad fund."""

import io
import json
import urllib.error
from datetime import datetime

import pytest

from buddy import funds, pricebook
from buddy.connectors.hub import ConnectorHub
from buddy.memory import Memory

ENV = {"BUDDY_SEC_CONTACT": "me@example.com"}


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "buddy.db")
    ConnectorHub(m)
    m.set(funds.FUNDS_KEY, json.dumps({n: "" for n in funds.DEFAULT} | {"Berkshire Hathaway": "1067983",
                                                                        "Broken Fund": "999"}))
    yield m
    pricebook.close_all()


def table(rows):
    body = "".join(
        f"<infoTable><nameOfIssuer>{n}</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>{c}</cusip>"
        f"<value>{v}</value><shrsOrPrnAmt><sshPrnamt>{s}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType>"
        f"</shrsOrPrnAmt>{f'<putCall>{pc}</putCall>' if pc else ''}</infoTable>" for n, c, v, s, pc in rows)
    return (f"<informationTable xmlns='http://www.sec.gov/edgar/document/thirteenf/informationtable'>{body}"
            f"</informationTable>").encode()


Q2 = [("APPLE INC", "037833100", 600, 300, ""), ("COCA COLA CO", "191216100", 250, 400, ""),
      ("OLD HOLDING CORP", "111111111", 150, 50, "")]
Q3 = [("APPLE INC", "037833100", 300, 150, ""), ("APPLE INC", "037833100", 100, 50, ""),   # two lots: one row
      ("COCA COLA CO", "191216100", 280, 440, ""), ("NEW IDEA INC", "222222222", 220, 90, ""),
      ("APPLE INC", "037833100", 20, 10, "Put")]


def sec(filings, seen=None):
    """filings: [(accession, quarter, filed, rows)], newest first."""
    def fetch(url, data=None, headers=None):
        if seen is not None:
            seen.append(url)
        if "CIK0000000999" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        if "/submissions/" in url:
            return json.dumps({"name": "BERKSHIRE HATHAWAY INC", "filings": {"recent": {
                "form": ["4"] + ["13F-HR"] * len(filings), "accessionNumber": ["x"] + [f[0] for f in filings],
                "filingDate": ["2026-11-01"] + [f[2] for f in filings],
                "reportDate": [""] + [f[1] for f in filings]}}})
        acc = url.split("/")[-2]
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"}]}})
        return table(next(f[3] for f in filings if f[0].replace("-", "") == acc))
    return fetch


A2, A3 = "0000950123-26-000002", "0000950123-26-000003"


def test_a_quarter_is_read_once_and_then_compared_with_the_next(memory):
    got = funds.refresh(memory, ENV, sec([(A2, "2026-06-30", "2026-08-14", Q2)]))
    assert got["Berkshire Hathaway"].startswith("new filing: quarter to 2026-06-30")
    assert got["Broken Fund"].startswith("failed")                      # and it didn't stop Berkshire
    first = funds.show(memory, "berkshire")
    assert first["top"][0] == {"holding": "Apple Inc", "value": 600, "share_of_fund": 0.6}
    assert "changes_since" not in first and "Not what the fund owns now" in first["note"]
    seen = []
    assert funds.refresh(memory, ENV, sec([(A2, "2026-06-30", "2026-08-14", Q2)], seen))[
        "Berkshire Hathaway"].startswith("up to date")
    assert not [u for u in seen if "Archives" in u]                     # nothing re-read

    funds.refresh(memory, ENV, sec([(A3, "2026-09-30", "2026-11-13", Q3), (A2, "2026-06-30", "2026-08-14", Q2)]))
    s = funds.show(memory, "Berkshire Hathaway")
    assert s["quarter_ending"] == "2026-09-30" and s["filed"] == "2026-11-13" and s["sec_name"].startswith("BERKSHIRE")
    assert s["top"][0]["holding"] == "Apple Inc" and s["top"][0]["value"] == 400     # the two lots together
    assert s["new"] == ["New Idea Inc", "Apple Inc (put options)"] and s["sold_out"] == ["Old Holding Corp"]
    assert s["trimmed"] == ["Apple Inc (-33% shares)"] and s["added"] == ["Coca Cola Co (+10% shares)"]
    assert s["changes_since"] == "2026-06-30" and s["top_10_share"] == 1.0


def test_values_before_2023_were_in_thousands(memory):
    funds.refresh(memory, ENV, sec([("0000950123-22-000001", "2022-09-30", "2022-11-14", Q2)]))
    assert funds.show(memory, "berkshire")["top"][0]["value"] == 600_000


def test_who_holds_a_company_by_name(memory):
    funds.refresh(memory, ENV, sec([(A3, "2026-09-30", "2026-11-13", Q3)]))
    got = funds.who_holds(memory, "Coca-Cola Company")
    assert got["held_by"][0]["fund"] == "Berkshire Hathaway" and got["held_by"][0]["quarter_ending"] == "2026-09-30"
    assert funds.who_holds(memory, "Tesla")["held_by"] == []
    with pytest.raises(Exception, match="Which company"):
        funds.who_holds(memory, "Inc")


def test_following_and_the_weekly_note(memory):
    assert "999" in json.dumps(funds.status(memory)) and "sec_name" in funds.status(memory)["note"]
    funds.unfollow(memory, "broken")
    assert "Broken Fund" not in funds.followed(memory)
    assert "Oakmark" in funds.follow(memory, "Oakmark", "0000872323") and funds.followed(memory)["Oakmark"] == "872323"
    funds.unfollow(memory, "Oakmark")
    note = funds.weekly(memory, ENV, sec([(A3, "2026-09-30", "2026-11-13", Q3)]))
    assert "Berkshire Hathaway (quarter to 2026-09-30, filed 2026-11-13)" in note and "already weeks old" in note
    assert funds.weekly(memory, ENV, sec([(A3, "2026-09-30", "2026-11-13", Q3)])) == ""   # nothing new: silent
    assert funds.due(memory, datetime(2026, 11, 16, 10, 0))
    memory.set(funds.WEEK_KEY, "2026-47")
    assert not funds.due(memory, datetime(2026, 11, 17, 10, 0)) and funds.due(memory, datetime(2026, 11, 23, 10, 0))
