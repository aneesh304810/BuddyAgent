"""Test Buddy's keys: one tiny real request to each service that has a key, and the provider's own answer when it
fails. Run on the server: sudo bash /opt/buddy/deploy/keys.sh  (never prints a key).

  Claude          lists models (free)
  Serper          one search (one credit)
  NJ Transit      signs in to the train API and the bus/light rail API (free; the token is never shown)
  free models     one 5-word chat with the model Buddy is set to use (Groq, GitHub Models, Gemini, Mistral,
                  Cerebras, OpenRouter, DeepSeek through OpenRouter)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

FIX = {401: "the key is wrong or expired: make a new one and save it with set.sh",
       403: "the key works but isn't allowed this: check its permissions (GitHub Models: Models read, or a classic "
            "token) or that the service is enabled on your account",
       404: "the model name is wrong or retired: set another with set.sh {setting}",
       400: "the request was refused, often a retired model: set another with set.sh {setting}",
       429: "over its free limit right now: it will work again later (or add credit)",
       402: "out of credit: top it up on the provider's site"}


def _post(url: str, body: dict, headers: dict, timeout: float = 25) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Buddy/1.0", **headers})
    return _send(req, timeout)


def _get(url: str, headers: dict, timeout: float = 25, limit: int = 20000) -> tuple[int, str]:
    return _send(urllib.request.Request(url, headers={"User-Agent": "Buddy/1.0", **headers}), timeout, limit)


def _send(req, timeout, limit: int = 20000) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310: fixed https endpoints
            return r.status, r.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def line(ok: bool, name: str, what: str, seconds: float, code: int, said: str, setting: str = "") -> str:
    if ok:
        return f"  OK    {name:<11} {what:<40} {seconds:.1f}s"
    if "credit" in said.lower():
        hint = "out of credit: buy more on the provider's site, or remove the key"
    elif code in (500, 502, 503, 529):
        hint = "busy or down on the provider's side right now: usually fine again later"
    else:
        hint = (FIX.get(code, "can't reach it (network or the service is down)" if code == 0 else "see the message")
            if code != 200 else "it answered, but not with a model reply: the address or headers are off (tell Claude)")
    said = " ".join(said.split())[:220]
    return (f"  FAIL  {name:<11} {what:<40} HTTP {code or '-'}: {said}\n"
            f"        fix: {hint.format(setting=setting)}")


def run(env=os.environ) -> list[str]:
    from .free_models import PROVIDERS
    out = []
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        t = time.monotonic()
        code, said = _get("https://api.anthropic.com/v1/models?limit=1",
                          {"x-api-key": key, "anthropic-version": "2023-06-01"})
        out.append(line(code == 200, "claude", "list models", time.monotonic() - t, code, said))
    serper = (env.get("BUDDY_SERPER_KEY") or "").strip()
    if serper:
        t = time.monotonic()
        code, said = _post("https://google.serper.dev/search", {"q": "weather", "num": 1}, {"X-API-KEY": serper})
        out.append(line(code == 200, "serper", "one search (1 credit)", time.monotonic() - t, code, said,
                        "BUDDY_SERPER_KEY"))
    else:
        out.append("  --    serper      no key set (google_search falls back to Claude's web search)")
    out += _njt(env)
    if (env.get("BUDDY_FREE_MODELS") or "").strip() != "1":
        out.append("  --    free models are switched off: set.sh BUDDY_FREE_MODELS and type 1")
    for name, p in PROVIDERS.items():
        k = (env.get(p["key"]) or "").strip()
        if not k:
            out.append(f"  --    {name:<11} no key ({p['key']})")
            continue
        model = (env.get(p["model_setting"]) or "").strip() or p["model"]
        if name in ("deepseek", "openrouter") and not (env.get(p["model_setting"]) or "").strip():
            model = _openrouter_pick(name) or model  # Buddy picks today's free model from OpenRouter's list
        t = time.monotonic()
        code, said = _post(f"{p['base']}/chat/completions",
                           {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Say hi."}]},
                           {"Authorization": f"Bearer {k}", **p.get("headers", {})})
        ok = code == 200 and '"choices"' in said
        out.append(line(ok, name, model[:40], time.monotonic() - t, code, said, p["model_setting"]))
        if not ok and code in (400, 404):  # what Buddy will switch to by itself
            from .free_models import pick_model
            lc, listing = _get(f"{p['base']}/models", {"Authorization": f"Bearer {k}"}, limit=2_000_000)
            try:
                ids = [m.get("id", "") for m in json.loads(listing).get("data", [])] if lc == 200 else []
            except ValueError:
                ids = []
            if (best := pick_model(name, ids, model)):
                out.append(f"        Buddy switches to {best} by itself on the next failure (or set it: "
                           f"set.sh {p['model_setting']})")
    return out


def _njt(env) -> list[str]:
    """NJ Transit: sign in to each API Buddy uses (trains; buses and light rail) and show its answer, token hidden."""
    from .transit import NJT_BASES, NJT_GTFS_BUS, _multipart, njt_reason
    user, pw = (env.get("BUDDY_NJT_USERNAME") or "").strip(), (env.get("BUDDY_NJT_PASSWORD") or "").strip()
    if not (user and pw):
        return ["  --    nj transit  no login (BUDDY_NJT_USERNAME and BUDDY_NJT_PASSWORD)"]
    out = []
    for name, urls in (("njt rail", [b + "getToken" for b in NJT_BASES]),
                       ("njt bus/lr", [NJT_GTFS_BUS + "authenticateUser"])):
        t = time.monotonic()
        for url in urls:  # like Buddy: the next host only when this one can't be reached
            body, ctype = _multipart({"username": user, "password": pw})
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": ctype, "User-Agent": "Buddy/1.0"})
            code, said = _send(req, 25)
            if code:
                break
        try:
            got = json.loads(said)
        except ValueError:
            got = said
        ok = code == 200 and isinstance(got, dict) and got.get("Authenticated") in (True, "True", "true") \
            and bool(got.get("UserToken"))
        if ok:
            out.append(f"  OK    {name:<11} {'sign in':<40} {time.monotonic() - t:.1f}s")
        else:
            reason = njt_reason(got).lower()
            hint = ("NJ Transit doesn't know this username: re-enter it exactly as you type it to sign in to "
                    "developer.njtransit.com (email or username): set.sh BUDDY_NJT_USERNAME") \
                if "missing user" in reason or "user account" in reason else \
                ("the username is right but the password isn't: set.sh BUDDY_NJT_PASSWORD") \
                if "password" in reason else \
                ("NJ Transit refused this login for this API: check that the API is approved under My APIs on "
                    "developer.njtransit.com, and that the username and password are the portal's (set.sh "
                    "BUDDY_NJT_USERNAME / BUDDY_NJT_PASSWORD)") if code in (200, 401, 403, 500) else \
                "can't reach it (network or the service is down)" if code == 0 else "see the message"
            out.append(f"  FAIL  {name:<11} {'sign in':<40} HTTP {code or '-'}: {njt_reason(got)}\n"
                       f"        fix: {hint}")
    return out


def _openrouter_pick(name: str) -> str:
    """Today's best free OpenRouter model for this provider's job, as Buddy chooses it (openrouter_catalog)."""
    from . import openrouter_catalog as cat
    code, said = _get(cat.URL, {}, limit=20_000_000)
    if code != 200:
        return ""
    try:
        models = cat.free_models(json.loads(said))
    except ValueError:
        return ""
    task = "code" if name == "deepseek" else "writing"
    return max(models, key=lambda m: cat.score(m, task))["id"] if models else ""


def trace(env, name: str) -> list[str]:
    """Every hop of one request to a provider, without following redirects: status, where it points, what came back."""
    from .free_models import PROVIDERS
    p = PROVIDERS[name]
    k = (env.get(p["key"]) or "").strip()
    if not k:
        return [f"{name}: no key"]

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    url = f"{p['base']}/chat/completions"
    body = json.dumps({"model": (env.get(p["model_setting"]) or "").strip() or p["model"], "max_tokens": 8,
                       "messages": [{"role": "user", "content": "Say hi."}]}).encode()
    out = []
    for hop in range(4):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "Buddy/1.0", "Authorization": f"Bearer {k}",
            **p.get("headers", {})})
        try:
            r = opener.open(req, timeout=25)
            status, headers, raw = r.status, r.headers, r.read(400)
        except urllib.error.HTTPError as e:
            status, headers, raw = e.code, e.headers, e.read(400)
        except Exception as e:
            return out + [f"  hop {hop + 1}: {url} -> {type(e).__name__}: {e}"]
        out.append(f"  hop {hop + 1}: POST {url} -> HTTP {status}, type {headers.get('Content-Type', '?')}, "
                   f"server {headers.get('Server', '?')}, location {headers.get('Location', '-')}, "
                   f"body {raw[:200]!r}")
        if status in (301, 302, 303, 307, 308) and headers.get("Location"):
            url = urllib.parse.urljoin(url, headers["Location"])
            continue
        break
    return out


def read_env(path: str) -> dict:
    """KEY=value lines of Buddy's settings file (as systemd reads it), without running anything."""
    env = {}
    for raw in open(path, encoding="utf-8").read().splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            k, v = raw.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


if __name__ == "__main__":
    env = read_env(sys.argv[1]) if len(sys.argv) > 1 else dict(os.environ)
    os.environ.update({k: v for k, v in env.items() if k == "ANTHROPIC_API_KEY"})  # Buddy's modules expect it
    if len(sys.argv) > 2:  # keys.sh github: every hop of one request, to see why it fails
        print(f"Tracing one request to {sys.argv[2]} (redirects not followed; the key is never shown):")
        for row in trace(env, sys.argv[2]):
            print(row)
        sys.exit(0)
    print("Testing Buddy's keys (one tiny request each; keys are never shown):")
    for row in run(env):
        print(row)
