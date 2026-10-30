#!/usr/bin/env python3
"""The install guard: every `pip install` and `npm install` in the sandbox goes through here.

  - Tools in Buddy's registry (/buddy/registry/tools.yaml) install at their pinned version.
  - Other packages are allowed only if they look established: they exist on PyPI or npm, were
    first published at least 90 days ago (fresh packages are where most malware hides), and their
    name isn't a lookalike of a popular package (typosquatting). Links, git URLs and archives
    from the web are refused: use the registry.
  - Every install is logged in /work/.tools/installs.log, so unvetted tools that keep being
    needed can be reviewed and added to the registry.

The sandbox itself is the real safety net (throwaway containers, no secrets, no route to your
server); this keeps what goes into it to known, established software.
"""

import datetime
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REGISTRY = Path(os.getenv("BUDDY_LIBRARY", "/buddy")) / "registry" / "tools.yaml"
LOG = Path("/work/.tools/installs.log")
REAL = {"pip": ["pip-real"], "npm": ["npm-real"]}
MIN_AGE_DAYS = 90


def load_registry() -> dict:
    try:
        import yaml

        return yaml.safe_load(REGISTRY.read_text()) or {}
    except Exception:
        return {}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def distance(a: str, b: str) -> int:
    """Edit distance, capped at 3 (only small differences matter here)."""
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return min(prev[-1], 3)


def lookalike(name: str, popular: list[str]) -> str:
    for known in popular:
        k = norm(known)
        if name != k and distance(name, k) <= (1 if len(k) <= 5 else 2):
            return known
    return ""


def first_published(kind: str, name: str, fetch=None) -> datetime.datetime | None:
    url = f"https://pypi.org/pypi/{name}/json" if kind == "pip" else f"https://registry.npmjs.org/{name}"
    try:
        if fetch:
            data = fetch(url)
        else:
            with urllib.request.urlopen(url, timeout=15) as r:  # https only  # nosec B310
                data = json.loads(r.read())
    except Exception:
        return None
    if kind == "pip":
        times = [f["upload_time_iso_8601"] for files in data.get("releases", {}).values() for f in files
                 if f.get("upload_time_iso_8601")]
        if not times:
            return None
        return min(datetime.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in times)
    created = (data.get("time") or {}).get("created")
    return datetime.datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None


def check(kind: str, raw: str, registry: dict, fetch=None) -> tuple[str, str, bool]:
    """(what to install, refusal reason or '', vetted?) for one requested package."""
    if re.match(r"(?i)^(https?:|git\+|file:|ssh:)|\.(whl|tar\.gz|zip|tgz)$", raw) or "://" in raw:
        return raw, "installing from links or archives isn't allowed; use the registry (find_tools)", False
    if raw.startswith((".", "/")):
        return raw, "", True  # the project in this workspace (e.g. pip install -e .)
    if kind == "pip":
        name = norm(re.split(r"[<>=!~\[ ;@]", raw, maxsplit=1)[0])
        pinned = bool(re.search(r"[<>=!~]", raw))
    else:
        body = raw[1:] if raw.startswith("@") else raw
        name = ("@" if raw.startswith("@") else "") + body.split("@", 1)[0]
        pinned = "@" in body
    for tool in registry.get("tools", []):
        if tool.get("kind") == kind and norm(tool.get("package", "")) == norm(name):
            spec = raw if pinned else (f"{tool['package']}=={tool['version']}" if kind == "pip"
                                       else f"{tool['package']}@{tool['version']}")
            return spec, "", True
    popular = [norm(p) for p in (registry.get("popular") or {}).get(kind, [])]
    if norm(name) in popular:
        return raw, "", False
    similar = lookalike(norm(name), popular)
    if similar:
        return raw, f"'{name}' looks like a misspelling of the popular '{similar}' (a common malware trick)", False
    born = first_published(kind, name, fetch)
    if born is None:
        return raw, f"'{name}' couldn't be found on {'PyPI' if kind == 'pip' else 'npm'}", False
    age = (datetime.datetime.now(datetime.timezone.utc) - born).days
    if age < MIN_AGE_DAYS:
        return raw, f"'{name}' was first published {age} days ago; packages under {MIN_AGE_DAYS} days old aren't allowed", False
    return raw, "", False


def packages(kind: str, args: list[str]) -> tuple[int, list[str]]:
    """Index of the subcommand and the package arguments, for install commands; (-1, []) otherwise."""
    install = {"pip": {"install"}, "npm": {"install", "i", "add"}}[kind]
    first = next((i for i, a in enumerate(args) if not a.startswith("-")), -1)
    if first < 0 or args[first] not in install:
        return -1, []
    out, j = [], first + 1
    while j < len(args):
        b = args[j]
        if b in ("-r", "--requirement"):
            out += _requirements(args[j + 1]) if j + 1 < len(args) else []
            j += 2
            continue
        if b in ("-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--registry") or \
                b.startswith(("--index-url=", "--extra-index-url=", "--find-links=", "--registry=")):
            out.append("://another-package-index")  # only the public PyPI / npm registries
            j += 1 if "=" in b else 2
            continue
        if b in ("-c", "--constraint", "-t", "--target", "--prefix", "--root"):
            j += 2
            continue
        if not b.startswith("-"):
            out.append(b)
        j += 1
    return first, out


def _requirements(path: str) -> list[str]:
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    return [l.split("#")[0].strip() for l in lines if l.split("#")[0].strip() and not l.startswith("-")]


def main(argv: list[str]) -> int:
    kind, args = argv[0], argv[1:]
    at, wanted = packages(kind, args)
    if at < 0 or not wanted:
        os.execvp(REAL[kind][0], REAL[kind] + args)
    registry = load_registry()
    final, refused, log = list(args), [], []
    for raw in wanted:
        spec, reason, vetted = check(kind, raw, registry)
        if reason:
            refused.append(f"  - {raw}: {reason}")
            continue
        if spec != raw and raw in final:
            final[final.index(raw)] = spec
        log.append({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "kind": kind,
                    "package": spec, "vetted": vetted})
    if refused:
        print("Install refused by Buddy's guard:\n" + "\n".join(refused) +
              "\nUse find_tools / install_tool for vetted tools, or pick an established package.", file=sys.stderr)
        return 3
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.writelines(json.dumps(entry) + "\n" for entry in log)
    except OSError:
        pass
    os.execvp(REAL[kind][0], REAL[kind] + final)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
