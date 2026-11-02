"""Diagnostics: events and measurements from the phone and this server, to find what makes Buddy feel
slow or deaf: the microphone, the phone putting the app to sleep, the network, or the server.

Never audio, never words: only times, levels, error codes and device states. Kept 30 days.
On by default; BUDDY_DIAGNOSTICS=0 on the server (or "Collect diagnostics" off in the app) stops it.
Self-contained on purpose: this file plus one-line hooks, so it's easy to switch off or revert.

  phone   mic (which mic, route changes, Bluetooth drops, recorder failures), each clip's levels
          (noise, speech, speech-to-noise ratio, clipping, clips dropped as too short), recognizer
          errors, app in background / foreground, battery saver and Doze, network type and changes,
          connection drops and reconnect time, ping to the server, Buddy's own voice heard back
  server  speech to text (engine, time, confidence, fallbacks), clips waiting in line, phone
          connections, slow replies with the server's load and free memory at that moment
"""

import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone

from .memory import Memory

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS diag_events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,                  -- UTC
    source TEXT NOT NULL,              -- app | server
    category TEXT NOT NULL,            -- mic | clip | speech | background | network | server | test
    event TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'    -- numbers and short labels only
);
CREATE INDEX IF NOT EXISTS diag_events_at ON diag_events (at);
"""
KEEP_DAYS = 30
CATEGORIES = ("mic", "clip", "speech", "background", "network", "server", "test")
SLOW_REPLY_MS = 4000
LOW_SNR_DB = 10.0


def enabled() -> bool:
    return os.getenv("BUDDY_DIAGNOSTICS", "1") != "0"


def _clean(data: dict | None) -> dict:
    """Numbers, booleans and short labels only: nothing that could carry what was said."""
    out = {}
    for k, v in (data or {}).items():
        if not isinstance(k, str) or len(k) > 40 or k in ("text", "words", "transcript", "pcm", "audio"):
            continue
        if isinstance(v, bool) or v is None:
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = round(float(v), 3) if isinstance(v, float) else v
        elif isinstance(v, str):
            out[k] = v[:40]
    return out


def server_load() -> dict:
    """CPU load (1 min, per CPU) and free memory, for slow moments."""
    out = {}
    try:
        out["load"] = round(os.getloadavg()[0] / max(1, os.cpu_count() or 1), 2)
    except (OSError, AttributeError):
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    out["mem_free_mb"] = int(line.split()[1]) // 1024
                    break
    except OSError:
        pass
    return out


def record(memory: Memory, source: str, category: str, event: str, data: dict | None = None,
           at: str | None = None) -> None:
    if not enabled() or category not in CATEGORIES:
        return
    try:
        memory.db.executescript(SCHEMA_SQL)
        memory.db.execute("INSERT INTO diag_events (at, source, category, event, data) VALUES (?, ?, ?, ?, ?)",
                          (at or datetime.now(timezone.utc).isoformat(), source[:10], category, str(event)[:40],
                           json.dumps(_clean(data))))
        memory.db.commit()
    except Exception:
        pass  # diagnostics never break anything


def server_event(memory: Memory, category: str, event: str, with_load: bool = False, **data) -> None:
    record(memory, "server", category, event, {**data, **(server_load() if with_load else {})})


def record_batch(memory: Memory, events: list) -> int:
    """A batch from the phone: [{at (epoch ms), category, event, data}]."""
    if not enabled():
        return 0
    n = 0
    now = datetime.now(timezone.utc)
    for e in (events or [])[:500]:
        if not isinstance(e, dict):
            continue
        try:
            at = datetime.fromtimestamp(float(e.get("at")) / 1000, timezone.utc)
            if abs((now - at).days) > KEEP_DAYS:
                continue
        except (TypeError, ValueError, OverflowError):
            at = now
        record(memory, "app", str(e.get("category", "")), str(e.get("event", "")), e.get("data"), at.isoformat())
        n += 1
    purge(memory)
    return n


def purge(memory: Memory) -> None:
    try:
        memory.db.execute("DELETE FROM diag_events WHERE at < ?",
                          ((datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(),))
        memory.db.commit()
    except Exception:
        pass


def _events(memory: Memory, since: str) -> list[dict]:
    memory.db.executescript(SCHEMA_SQL)
    return [{**dict(r), "data": json.loads(r["data"] or "{}")}
            for r in memory.db.execute("SELECT * FROM diag_events WHERE at >= ? ORDER BY at", (since,))]


def _median(vals: list[float]) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)


def _p95(vals: list[float]) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    return round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 1) if vals else None


def health(memory: Memory, since: str) -> dict:
    """The Experience health summary: microphone, background, network, server, and the slowest moments."""
    ev = _events(memory, since)

    def pick(category, event=None):
        return [e for e in ev if e["category"] == category and (event is None or e["event"] == event)]
    # microphone and clips
    clips = pick("clip", "sent")
    mics = {}
    for m in sorted({c["data"].get("mic", "?") for c in clips}):
        mine = [c for c in clips if c["data"].get("mic", "?") == m]
        snr = [c["data"].get("snr_db") for c in mine]
        mics[m] = {"clips": len(mine), "snr_db": _median(snr),
                   "low_snr_pct": round(100 * sum(1 for v in snr if v is not None and v < LOW_SNR_DB) / len(mine)),
                   "noise_db": _median([c["data"].get("noise_db") for c in mine]),
                   "speech_db": _median([c["data"].get("speech_db") for c in mine]),
                   "clipped_pct": round(100 * sum(1 for c in mine if c["data"].get("clipped")) / len(mine))}
    dropped = {}
    for c in pick("clip", "dropped"):
        reason = c["data"].get("reason", "?")
        dropped[reason] = dropped.get(reason, 0) + 1
    mic_events = {}
    for e in pick("mic"):
        mic_events[e["event"]] = mic_events.get(e["event"], 0) + 1
    # speech engines and errors
    stt = pick("speech", "stt")
    engines = {}
    for name in sorted({s["data"].get("engine", "?") for s in stt}):
        mine = [s for s in stt if s["data"].get("engine", "?") == name]
        engines[name] = {"clips": len(mine), "ms": _median([s["data"].get("ms") for s in mine]),
                         "p95_ms": _p95([s["data"].get("ms") for s in mine]),
                         "confidence": _median([s["data"].get("confidence") for s in mine])}
    errors = {}
    for e in pick("speech"):
        if e["event"] in ("recognizer_error", "groq_fallback", "echo_dropped"):
            key = e["event"] + (f": {e['data'].get('error')}" if e["data"].get("error") else "")
            errors[key] = errors.get(key, 0) + 1
    # background
    background = {}
    for e in pick("background"):
        background[e["event"]] = background.get(e["event"], 0) + 1
    states = [e for e in pick("background", "power_state")]
    saver = round(100 * sum(1 for s in states if s["data"].get("battery_saver")) / len(states)) if states else None
    # network
    pings = [e["data"].get("rtt_ms") for e in pick("network", "ping")]
    drops = pick("network", "disconnected")
    reconnects = [e["data"].get("offline_ms") for e in pick("network", "reconnected")]
    types = {}
    for e in pick("network", "ping"):
        t = e["data"].get("net", "?")
        types[t] = types.get(t, 0) + 1
    days = max(1.0, (datetime.now(timezone.utc) - datetime.fromisoformat(since)).total_seconds() / 86400)
    # server
    slow = pick("server", "slow_reply")
    busy = [s for s in slow if (s["data"].get("load") or 0) >= 0.8]
    queue = [e["data"].get("wait_ms") for e in pick("server", "clip_queued")]
    tips = _tips(mics, engines, pings, drops, days, background, saver, slow, busy, dropped, len(clips))
    return {
        "microphone": {"by_mic": mics, "dropped_clips": dropped, "events": mic_events},
        "speech": {"engines": engines, "errors": errors},
        "background": {"events": background, "battery_saver_pct": saver},
        "network": {"ping_ms": _median(pings), "ping_p95_ms": _p95(pings), "disconnects": len(drops),
                    "disconnects_per_day": round(len(drops) / days, 1), "reconnect_ms": _median(reconnects),
                    "by_network": types},
        "server": {"slow_replies": len(slow), "slow_while_busy": len(busy),
                   "load": _median([s["data"].get("load") for s in slow]),
                   "clip_wait_ms": _median(queue)},
        "slowest": slowest_moments(memory, since, ev),
        "tips": tips,
        "events": len(ev),
        "on": enabled(),
    }


def _tips(mics, engines, pings, drops, days, background, saver, slow, busy, dropped, clips) -> list[str]:
    tips = []
    for m, x in mics.items():
        if x["clips"] >= 5 and x["low_snr_pct"] >= 30:
            tips.append(f"The {m} mic is noisy in {x['low_snr_pct']}% of clips (speech only {x['snr_db']} dB over the "
                        "background): try the other mic, or turn on the noise suppressor in Settings.")
        if x["clips"] >= 5 and x["clipped_pct"] >= 15:
            tips.append(f"The {m} mic distorts (too loud) in {x['clipped_pct']}% of clips: hold it a little further away.")
    local = engines.get("local")
    if local and local["clips"] >= 3 and (local["ms"] or 0) > 1200:
        tips.append(f"Speech to text on your server takes {local['ms'] / 1000:.1f} s: Groq (BUDDY_GROQ_KEY) is about "
                    "5× faster.")
    ping = _median(pings)
    if ping and ping > 250:
        tips.append(f"Ping to your server is {ping:.0f} ms: check `tailscale ping` shows 'direct', not 'via DERP'.")
    if len(drops) / days >= 5:
        tips.append(f"The connection drops about {len(drops) / days:.0f} times a day: check the phone's battery "
                    "settings (Buddy unrestricted) and Wi-Fi to mobile switching.")
    if background.get("service_restarted", 0) >= 2 or background.get("service_killed", 0) >= 1:
        tips.append("Android stopped Buddy's listening service: set Buddy's battery use to Unrestricted.")
    if saver is not None and saver >= 50:
        tips.append("Battery saver was on most of the time: it slows the network and can pause listening.")
    if slow and len(busy) >= max(2, len(slow) // 2):
        tips.append(f"{len(busy)} of {len(slow)} slow replies came while the server was busy: a 2-CPU droplet would help.")
    short = dropped.get("too_short", 0)
    if clips >= 10 and short >= clips // 2:
        tips.append("Many short sounds were thrown away as too short: if words get cut, set 'Pauses when I talk' longer.")
    return tips


def slowest_moments(memory: Memory, since: str, ev: list[dict] | None = None, limit: int = 10) -> list[dict]:
    """The slowest or missed utterances, each with what the phone and server saw around it."""
    from .insights import VOICE_SQL

    memory.db.executescript(VOICE_SQL)
    rows = [dict(r) for r in memory.db.execute(
        "SELECT * FROM voice_timings WHERE at >= ? ORDER BY (missed != '') DESC, total_ms DESC LIMIT ?", (since, limit))]
    ev = ev if ev is not None else _events(memory, since)
    out = []
    for r in rows:
        t = datetime.fromisoformat(r["at"])

        def near(category, event, before=60, after=5):
            hits = [e for e in ev if e["category"] == category and e["event"] == event
                    and -before <= (datetime.fromisoformat(e["at"]) - t).total_seconds() <= after]
            return hits[-1]["data"] if hits else None
        clip, stt, slow, ping = near("clip", "sent"), near("speech", "stt"), near("server", "slow_reply", 60, 60), \
            near("network", "ping", 300, 60)
        out.append({"at": r["at"], "missed": r["missed"], "total_ms": r["total_ms"], "steps": {
            "pause": r["wait_ms"], "network": r["network_ms"], "speech_to_text": r["stt_ms"],
            "to_buddy": r["to_thinking_ms"], "first_word": r["first_word_ms"]}, "engine": r["engine"],
            "mic": (clip or {}).get("mic"), "snr_db": (clip or {}).get("snr_db"),
            "confidence": (stt or {}).get("confidence"), "server_load": (slow or {}).get("load"),
            "ping_ms": (ping or {}).get("rtt_ms"), "net": (ping or {}).get("net")})
    return out


def rate_test(memory: Memory, r: dict) -> dict:
    """'Test my setup' results from the phone -> a check list with fixes (and kept as a test event)."""
    checks = []

    def check(name, ok, value, fix=""):
        checks.append({"name": name, "ok": bool(ok), "value": value, "fix": "" if ok else fix})
    noise, speech, snr = r.get("noise_db"), r.get("speech_db"), r.get("snr_db")
    mic = r.get("mic") or "phone"
    if noise is not None:
        check("Background noise", noise < 50, f"{noise:.0f} dB",
              "It's noisy here: the earbud mic or the noise suppressor (Settings) helps.")
    if speech is not None:
        check("Your voice level", 45 <= speech <= 85, f"{speech:.0f} dB",
              "Too quiet: speak up or bring the mic closer." if speech < 45 else "Too loud: hold the mic further away.")
    if snr is not None:
        check("Voice over background", snr >= LOW_SNR_DB, f"{snr:.0f} dB",
              f"Your voice is only {snr:.0f} dB over the background on the {mic} mic: try the other mic or a quieter spot.")
    if r.get("clipped") is not None:
        check("No distortion", not r["clipped"], "clean" if not r["clipped"] else "distorted",
              "The recording distorted: hold the mic further away.")
    if r.get("rtt_ms") is not None:
        check("Ping to your server", r["rtt_ms"] < 250, f"{r['rtt_ms']:.0f} ms",
              "Slow link: check `tailscale ping` is 'direct', and Wi-Fi vs mobile data.")
    if r.get("stt_ms") is not None:
        check("Speech to text", r["stt_ms"] < 1200, f"{r['stt_ms'] / 1000:.1f} s ({r.get('engine', '?')})",
              "Slow: set BUDDY_GROQ_KEY on the server for Groq (about 5× faster than the local model).")
    if r.get("heard_ok") is not None:
        check("Understood the test sentence", r["heard_ok"], "yes" if r["heard_ok"] else "no",
              "It misheard the sentence: check the mic level and background noise above.")
    if r.get("tts_ms") is not None:
        check("Buddy's voice starts quickly", r["tts_ms"] < 1500, f"{r['tts_ms'] / 1000:.1f} s",
              "Slow to speak: install the voice's data in phone Settings → Text-to-speech.")
    for flag, name, fix in (("battery_ok", "Battery: unrestricted", "Settings → Battery → Buddy → Unrestricted."),
                            ("notifications_ok", "Notification access", "Allow it in Settings for ride and money alerts.")):
        if r.get(flag) is not None:
            check(name, r[flag], "yes" if r[flag] else "no", fix)
    record(memory, "app", "test", "setup_test", {k: v for k, v in r.items() if k != "heard"})
    passed = sum(c["ok"] for c in checks)
    return {"checks": checks, "passed": passed, "total": len(checks),
            "summary": "All good." if passed == len(checks) else f"{len(checks) - passed} to fix."}


def export(memory: Memory, since: str, fmt: str = "csv") -> tuple[str, str]:
    """All diagnostics and voice timings in the period, as CSV (one row per event) or JSON."""
    from .insights import VOICE_SQL

    ev = _events(memory, since)
    memory.db.executescript(VOICE_SQL)
    voice = [dict(r) for r in memory.db.execute("SELECT * FROM voice_timings WHERE at >= ? ORDER BY at", (since,))]
    if fmt == "json":
        return json.dumps({"since": since, "events": ev, "voice_timings": voice}, indent=1), "application/json"
    buf = io.StringIO()
    keys = sorted({k for e in ev for k in e["data"]} | {k for v in voice for k in v if k not in ("id", "at")})
    w = csv.writer(buf)
    w.writerow(["at", "source", "category", "event", *keys])
    for e in ev:
        w.writerow([e["at"], e["source"], e["category"], e["event"], *[e["data"].get(k, "") for k in keys]])
    for v in voice:
        w.writerow([v["at"], "app", "voice", "timing", *[v.get(k, "") for k in keys]])
    return buf.getvalue(), "text/csv"
