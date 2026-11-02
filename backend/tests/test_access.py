"""Access control: role boundary for agents, access review, revoke and automatic de-provisioning."""

import datetime as dt
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from buddy.agents import core_only

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def test_agents_never_get_core_secrets():
    for name in ("BUDDY_PLAID_SECRET", "BUDDY_PLAID_CLIENT_ID", "BUDDY_DB_KEY", "BUDDY_TOKEN", "ANTHROPIC_API_KEY",
                 "BUDDY_SIMPLEFIN_URL", "BUDDY_TAILSCALE_API_KEY"):
        assert core_only(name)
    assert not core_only("BUDDY_EMAIL_PASSWORD") and not core_only("BUDDY_EMAIL_1_ADDRESS")


def test_agent_env_from_wildcard_filters_core_secrets(monkeypatch):
    from buddy import agents
    monkeypatch.setenv("BUDDY_PLAID_SECRET", "s")
    monkeypatch.setenv("BUDDY_EMAIL_PASSWORD", "p")
    seen = {}

    class Stop(Exception):
        pass

    def params(**kw):
        seen.update(kw["env"] or {})
        raise Stop

    monkeypatch.setattr(agents, "StdioServerParameters", params)
    spec = agents.AgentSpec(name="x", command=["true"], env_from=["BUDDY_*"])
    hub = agents.AgentRegistry.__new__(agents.AgentRegistry)
    import asyncio
    with pytest.raises(Stop):
        asyncio.run(hub._connect(spec))
    assert "BUDDY_EMAIL_PASSWORD" in seen and "BUDDY_PLAID_SECRET" not in seen


@pytest.fixture
def access(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("access", DEPLOY / "access.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "APPROVED", tmp_path / "approved.json")
    monkeypatch.setattr(mod, "REVIEWS", tmp_path / "reviews")
    monkeypatch.setattr(mod, "LOG", tmp_path / "access.log")
    monkeypatch.setattr(mod, "KEY_DATES", tmp_path / "key-dates")
    monkeypatch.setattr(mod, "SUDOERS_D", tmp_path / "sudoers.d")
    (tmp_path / "sudoers.d").mkdir()
    (tmp_path / "key-dates").write_text("BUDDY_PLAID_SECRET=2026-11-01\n")
    home = tmp_path / "home"
    state = {"users": [SimpleNamespace(pw_name="root", pw_uid=0, pw_dir=str(home / "root"), pw_shell="/bin/bash"),
                       SimpleNamespace(pw_name="buddy", pw_uid=998, pw_dir="/var/lib/buddy", pw_shell="/usr/sbin/nologin"),
                       SimpleNamespace(pw_name="aneesh", pw_uid=1000, pw_dir=str(home / "aneesh"), pw_shell="/bin/bash")],
             "sudo": ["aneesh"], "calls": [], "devices": []}
    for u in ("root", "aneesh"):
        (home / u / ".ssh").mkdir(parents=True)
    (home / "aneesh" / ".ssh" / "authorized_keys").write_text("ssh-ed25519 AAAA aneesh@laptop\n")
    monkeypatch.setattr(mod.pwd, "getpwall", lambda: state["users"])
    monkeypatch.setattr(mod.pwd, "getpwnam", lambda n: next(u for u in state["users"] if u.pw_name == n) if any(
        u.pw_name == n for u in state["users"]) else (_ for _ in ()).throw(KeyError(n)))
    monkeypatch.setattr(mod.grp, "getgrnam", lambda g: SimpleNamespace(gr_mem=state["sudo"]) if g == "sudo"
                        else (_ for _ in ()).throw(KeyError(g)))

    def sh(*args, **kw):
        state["calls"].append(args)
        out = ""
        if args[0] == "passwd":
            out = f"{args[2]} P 2026-01-01"
        elif args[0] == "ssh-keygen":
            text = kw.get("input") or (Path(args[2]).read_text() if Path(args[2]).exists() else "")
            out = "".join(f"256 SHA256:{line.split()[2]} {line.split()[2]} (ED25519)\n"
                          for line in text.splitlines() if line.strip())
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(mod, "sh", sh)
    monkeypatch.setattr(mod, "tailnet", lambda: {"devices": state["devices"],
                                                 "users": sorted({d["user"] for d in state["devices"] if d["user"]})})
    return mod, state, home


def device(name, user="me@gmail.com", days=0, me=False, online=False):
    seen = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    return {"name": name, "dns": f"{name}.tail.ts.net", "id": f"id-{name}", "user": user, "os": "linux",
            "online": online, "last_seen": seen, "tags": [], "key_expiry": "", "self": me}


def test_review_records_evidence_and_approves(access):
    mod, state, home = access
    state["devices"] = [device("buddy", me=True), device("fold", online=True)]
    answers = iter(["y"] * len(mod.MANUAL_CHECKS) + ["Aneesh", "y"])
    record = mod.review(ask=lambda q: next(answers))
    assert record["approved"] and record["reviewer"] == "Aneesh" and not record["follow_up"]
    ok = json.loads(mod.APPROVED.read_text())
    assert ok["users"] == ["aneesh", "root"] and ok["admins"] == ["aneesh", "root"]
    assert ok["ssh_keys"] == ["SHA256:aneesh@laptop"] and ok["devices"] == ["buddy", "fold"]
    saved = list(mod.REVIEWS.glob("review-*.json"))
    assert len(saved) == 1 and "BUDDY_PLAID_SECRET" in saved[0].read_text()
    assert "access review by Aneesh: approved" in mod.LOG.read_text()
    assert mod.review_due() is None  # done for this quarter


def test_weekly_check_flags_review_due_and_unapproved_access(access):
    mod, state, home = access
    found = []
    mod.check(lambda *a: found.append(a))
    assert found[0][1] == "No access review on record"
    state["devices"] = [device("buddy", me=True), device("fold")]
    answers = iter(["y"] * len(mod.MANUAL_CHECKS) + ["Aneesh", "y"])
    mod.review(ask=lambda q: next(answers))
    # later: a new tailnet user's device, an old tablet, a new server account
    state["devices"] += [device("strangers-pc", user="x@y.com"), device("old-tab", days=90)]
    state["users"].append(SimpleNamespace(pw_name="temp", pw_uid=1001, pw_dir=str(home / "temp"), pw_shell="/bin/sh"))
    found.clear()
    mod.check(lambda *a: found.append(a))
    titles = " | ".join(f[1] for f in found)
    assert "Unapproved account: temp" in titles and "Unapproved tailnet device: strangers-pc" in titles
    assert "Unapproved tailnet user: x@y.com" in titles and "old-tab not seen for 90 days" in titles
    assert "review is due" not in titles


def test_enforce_locks_unapproved_accounts_and_keys(access, monkeypatch):
    mod, state, home = access
    answers = iter(["y"] * len(mod.MANUAL_CHECKS) + ["Aneesh", "y"])
    mod.review(ask=lambda q: next(answers))
    # someone adds an account and a key to root
    state["users"].append(SimpleNamespace(pw_name="temp", pw_uid=1001, pw_dir=str(home / "temp"), pw_shell="/bin/sh"))
    (home / "root" / ".ssh" / "authorized_keys").write_text("ssh-ed25519 BBBB intruder@x\n")
    done = mod.enforce()
    assert any(c[0] == "usermod" and "--lock" in c and c[-1] == "temp" for c in state["calls"])
    assert (home / "root" / ".ssh" / "authorized_keys").read_text() == ""
    assert (home / "aneesh" / ".ssh" / "authorized_keys").read_text().strip()  # approved key kept
    assert done and "auto-revoked (not approved) user temp" in mod.LOG.read_text()
    monkeypatch.setenv("BUDDY_ACCESS_AUTO_REVOKE", "0")
    state["calls"].clear()
    assert mod.enforce() == [] and state["calls"] == []


def test_enforce_does_nothing_before_the_first_review(access):
    mod, state, home = access
    assert mod.enforce() == []
    assert not any(c[0] == "usermod" for c in state["calls"])


def test_offboard_revokes_everything_and_root_is_kept(access, monkeypatch):
    mod, state, home = access
    state["devices"] = [device("buddy", me=True), device("aneesh-laptop", user="aneesh@gmail.com")]
    monkeypatch.setattr(mod, "tailscale_api_key", lambda: "tskey-api-x")
    deleted = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=0: deleted.append(req.full_url) or Resp())
    steps = mod.offboard("aneesh")
    assert any("password locked" in s for s in steps) and any("removed from sudo" in s for s in steps)
    assert not (home / "aneesh" / ".ssh" / "authorized_keys").exists()
    assert any("removed from the tailnet" in s for s in steps)
    assert deleted == ["https://api.tailscale.com/api/v2/device/id-aneesh-laptop"]
    assert mod.revoke_user("root")[0].startswith("root can't be revoked")
    assert mod.revoke_device("buddy")[0].startswith("That's this server")
    assert "offboarded aneesh" in mod.LOG.read_text()


def test_device_removal_without_api_key_says_where(access, monkeypatch):
    mod, state, home = access
    state["devices"] = [device("old-tab", days=90)]
    monkeypatch.setattr(mod, "tailscale_api_key", lambda: "")
    assert "login.tailscale.com/admin/machines" in mod.revoke_device("old-tab")[0]
