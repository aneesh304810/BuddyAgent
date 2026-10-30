"""Security: sealed settings, keys kept out of logs, the weekly check and its report."""

import datetime as dt
import importlib.util
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import buddy
from buddy import health
from buddy.privacy import SecretFilter, redact_secrets

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def test_sealed_settings_load_from_the_credentials_folder(tmp_path, monkeypatch):
    (tmp_path / "buddy-env").write_text('# comment\nBUDDY_X_KEY="abc123"\nBUDDY_Y=plain\nBUDDY_KEEP=new\n')
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("BUDDY_KEEP", "old")
    monkeypatch.delenv("BUDDY_X_KEY", raising=False)
    monkeypatch.delenv("BUDDY_Y", raising=False)
    buddy._load_sealed_settings()
    assert os.environ["BUDDY_X_KEY"] == "abc123" and os.environ["BUDDY_Y"] == "plain"
    assert os.environ["BUDDY_KEEP"] == "old"  # never overrides what systemd already set


def test_keys_never_reach_the_logs(caplog):
    key = "sk-ant-api03-" + "A" * 40
    assert key not in redact_secrets(f"POST failed with key {key}")
    logger = logging.getLogger("buddy.test.secrets")
    handler = logging.Handler()
    handler.addFilter(SecretFilter())
    seen = []
    handler.emit = lambda record: seen.append(record.getMessage())
    logger.addHandler(handler)
    logger.warning("token %s and ghp_%s", key, "b" * 30)
    assert seen and "sk-ant-api03" not in seen[0] and "ghp_" not in seen[0] and "[secret hidden]" in seen[0]


def test_weekly_security_report_feeds_the_health_check(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    path = tmp_path / "security.json"
    path.write_text(json.dumps({"at": now.isoformat(), "status": "act", "done": ["Ubuntu security updates"],
                                "findings": [{"severity": "act", "area": "security", "title": "Funnel is on",
                                              "detail": "", "fix": "tailscale funnel reset"},
                                             {"severity": "info", "area": "security", "title": "fyi",
                                              "detail": "", "fix": ""}]}))
    report = health.security_report(now, str(path))
    assert [f["title"] for f in report["findings"]] == ["Funnel is on"] and report["done"]
    old = now - dt.timedelta(days=15)
    path.write_text(json.dumps({"at": old.isoformat(), "status": "good", "findings": []}))
    assert "last ran 15 days ago" in health.security_report(now, str(path))["findings"][0]["title"]


def _lib(tmp_path, body):
    script = (f'source "{DEPLOY}/secrets-lib.sh"\nPLAIN_ENV={tmp_path}/buddy.env\nSEALED_ENV={tmp_path}/buddy.env.cred\n'
              f'KEY_DATES={tmp_path}/key-dates\n' + body)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_secrets_helper_dates_changed_keys_only(tmp_path):
    (tmp_path / "buddy.env").write_text("BUDDY_TOKEN=aaa\nBUDDY_GROQ_KEY=old\n")
    r = _lib(tmp_path, 'secrets_open\nsed -i "s/old/new/" "$ENV_FILE"\necho "BUDDY_NEW_KEY=x" >> "$ENV_FILE"\n'
                       'secrets_close\n')
    assert r.returncode == 0, r.stderr
    dates = dict(l.split("=") for l in (tmp_path / "key-dates").read_text().split())
    today = dt.date.today().isoformat()
    assert dates == {"BUDDY_TOKEN": "unknown", "BUDDY_GROQ_KEY": today, "BUDDY_NEW_KEY": today}
    assert oct((tmp_path / "buddy.env").stat().st_mode)[-3:] == "600"
    assert "aaa" not in (tmp_path / "key-dates").read_text()  # names and dates only


@pytest.mark.skipif(os.geteuid() != 0 or not shutil.which("systemd-creds"), reason="needs root and systemd-creds")
def test_sealed_mode_keeps_no_plain_copy(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("BUDDY_TOKEN=abc\n")
    enc = subprocess.run(["systemd-creds", "encrypt", "--name=buddy-env", str(plain), str(tmp_path / "buddy.env.cred")],
                         capture_output=True, text=True)
    if enc.returncode != 0:
        pytest.skip("systemd-creds can't encrypt here: " + enc.stderr[-100:])
    plain.unlink()
    r = _lib(tmp_path, 'secrets_open\necho "BUDDY_GROQ_KEY=k" >> "$ENV_FILE"\nT=$ENV_FILE\nsecrets_close\n'
                       'test ! -e "$T" && echo SHREDDED\n'
                       'systemd-creds decrypt --name=buddy-env "$SEALED_ENV" - | grep -c BUDDY_GROQ_KEY\n')
    assert r.returncode == 0, r.stderr
    assert "SHREDDED" in r.stdout and r.stdout.strip().endswith("1")
    assert not (tmp_path / "buddy.env").exists()


@pytest.fixture
def maintain(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("maintain", DEPLOY / "maintain.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PLAIN_ENV", tmp_path / "buddy.env")
    monkeypatch.setattr(mod, "SEALED_ENV", tmp_path / "buddy.env.cred")
    monkeypatch.setattr(mod, "KEY_DATES", tmp_path / "key-dates")
    mod.findings.clear()
    return mod


def test_weekly_check_flags_weak_token_old_and_plain_keys(maintain, tmp_path):
    (tmp_path / "buddy.env").write_text("x")
    (tmp_path / "buddy.env").chmod(0o600)
    old = (dt.date.today() - dt.timedelta(days=200)).isoformat()
    (tmp_path / "key-dates").write_text(f"ANTHROPIC_API_KEY={old}\nBUDDY_GROQ_KEY=unknown\n")
    maintain.check_secrets({"BUDDY_TOKEN": "short", "ANTHROPIC_API_KEY": "sk", "BUDDY_GROQ_KEY": "g",
                            "BUDDY_USER_NAME": "Sam"})
    titles = " | ".join(f["title"] for f in maintain.findings)
    assert "unencrypted" in titles and "token is short" in titles and "older than 90 days" in titles
    assert "no date yet" in titles and "BUDDY_USER_NAME" not in json.dumps(maintain.findings)
    assert "sk" not in json.dumps([f["detail"] for f in maintain.findings]).replace("keys", "")


def test_weekly_check_spots_exposed_listeners_and_funnel(maintain, monkeypatch):
    outputs = {
        "ss": "tcp LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:* users:((\"uvicorn\"))\n"
              "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\"))\n"
              "tcp LISTEN 0 128 0.0.0.0:6379 0.0.0.0:* users:((\"redis-server\"))\n",
        "ufw": "Status: active\nDefault: deny (incoming), allow (outgoing)",
        "sshd": "passwordauthentication no",
        "tailscale": "https://buddy.ts.net (Funnel on)",
    }
    monkeypatch.setattr(maintain, "sh", lambda *a, **k: subprocess.CompletedProcess(a, 0, outputs.get(a[0], ""), ""))
    monkeypatch.setattr(maintain.shutil, "which", lambda name: "/usr/bin/" + name)
    maintain.check_network()
    titles = [f["title"] for f in maintain.findings]
    assert any("all network interfaces" in t for t in titles)
    assert "6379" in maintain.findings[0]["detail"] and ":22" not in maintain.findings[0]["detail"]
    assert any("Funnel" in t for t in titles)
