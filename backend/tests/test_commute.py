"""The weekday commute, learned from where the phone goes; heads-ups before leaving."""

from datetime import datetime, timedelta, timezone

from buddy import commute, transit
from buddy.maps import Maps
from buddy.memory import Memory
from buddy.tools import user_tz

HOME = (40.7768, -74.0117)      # Port Imperial: a Hudson-Bergen Light Rail stop
NEWPORT = (40.7270, -74.0337)   # PATH Newport
WTC = (40.7126, -74.0099)       # PATH WTC
WORK = (40.7075, -74.0113)      # an office near WTC


def _weekdays(n=8):
    d = datetime.now(user_tz()).date() - timedelta(days=1)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def _log(maps, day, hhmm, where):
    h, m = map(int, hhmm.split(":"))
    at = datetime(day.year, day.month, day.day, h, m, tzinfo=user_tz()).astimezone(timezone.utc)
    maps.log_location(*where, at=at)


def _commute_days(m):
    maps = Maps(m)
    for day in _weekdays():
        for hhmm, where in (("02:00", HOME), ("06:30", HOME), ("08:00", HOME),
                            ("08:12", (40.7590, -74.0236)),  # a light rail stop on the way
                            ("08:25", NEWPORT), ("08:40", WTC), ("08:50", WORK), ("11:00", WORK), ("14:00", WORK),
                            ("17:30", WORK), ("17:42", WTC), ("17:55", NEWPORT), ("18:20", HOME), ("23:00", HOME)):
            _log(maps, day, hhmm, where)
    maps.learn_places()
    return maps


def test_learns_times_and_lines_and_saves_them(monkeypatch, tmp_path):
    m = Memory(tmp_path / "b.db")
    _commute_days(m)
    r = commute.learn(m)
    assert r["morning"]["leave"] == "08:12" and r["morning"]["arrive"] == "08:50"
    assert r["evening"]["leave"] == "17:42" and r["evening"]["arrive"] == "18:20"
    assert set(r["lines"]) == {"PATH", "Hudson-Bergen Light Rail"}
    assert r["morning"]["path_station"] == "NEW" and r["evening"]["path_station"] == "WTC"
    said = commute.save(m, r)
    assert "learned your weekday commute" in said and "8:12 AM" in said and "stop commute alerts" in said
    assert any(f.startswith(commute.FACT) for f in m.recall("commute"))
    watched = {w["line"]: w for w in transit.Transit(m).watches()}
    assert watched["PATH"]["station"] == "Newport" and watched["PATH"]["days"] == "weekdays"
    assert "07:27-09:05" in watched["PATH"]["times"]
    assert commute.save(m, r) == ""  # unchanged: not said again


def test_heads_up_before_leaving_with_trains_and_alerts(monkeypatch, tmp_path):
    m = Memory(tmp_path / "b.db")
    m.set(commute.KEY, '{"morning": {"leave": "08:12", "arrive": "08:50", "path_station": "NEW"}, '
                       '"lines": ["PATH"], "learned": "2026-11-09"}')
    monkeypatch.setattr(transit.Transit, "path_arrivals",
                        lambda self, st: [{"to": "World Trade Center", "in": "4 min"}, {"to": "World Trade Center", "in": "11 min"}])
    monkeypatch.setattr(transit.Transit, "status", lambda self, line="": {"lines": [
        {"line": "PATH", "ok": False, "alerts": [{"text": "Delays of 10 minutes", "kind": "PATH alert"}]}]})
    monday = datetime(2026, 11, 9, 7, 55)
    got = commute.nudges(m, monday)
    assert len(got) == 1 and got[0]["urgent"] and "World Trade Center 4 min" in got[0]["text"]
    assert "Delays of 10 minutes" in got[0]["text"] and got[0]["key"] == "commute:morning:2026-11-09"
    assert commute.nudges(m, datetime(2026, 11, 9, 7, 30)) == []      # too early
    assert commute.nudges(m, datetime(2026, 11, 14, 7, 55)) == []     # Saturday
    m.set(commute.OFF_KEY, "1")
    assert commute.nudges(m, monday) == []


def test_not_enough_history_says_so(tmp_path):
    m = Memory(tmp_path / "b.db")
    assert commute.learn(m) is None
    assert "can't see a weekday routine" in commute.run_tool(m, {"action": "relearn"})
