#!/usr/bin/env python3
"""Install a tool from Buddy's registry into this workspace:  install_tool.py NAME [NAME...]

pip and npm tools install at their pinned version (into /work/.tools, so they stay for the rest
of the task); binary tools are downloaded, checked against their SHA-256 and unpacked into
/work/.tools/bin. Built-in tools are already there. Anything not in the registry is refused here
(the pip / npm guard decides about other packages).
"""

import hashlib
import io
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pkgguard import load_registry, norm  # noqa: E402

BIN = Path("/work/.tools/bin")


def install(tool: dict) -> str:
    kind = tool.get("kind")
    if kind == "builtin":
        return f"{tool['name']}: already installed"
    if kind == "pip":
        done = subprocess.run(["pip-real", "install", "--user", "--quiet", f"{tool['package']}=={tool['version']}"])
        return f"{tool['name']}: {'installed' if done.returncode == 0 else 'FAILED'} ({tool['version']})"
    if kind == "npm":
        done = subprocess.run(["npm-real", "install", "-g", "--silent", f"{tool['package']}@{tool['version']}"])
        return f"{tool['name']}: {'installed' if done.returncode == 0 else 'FAILED'} ({tool['version']})"
    if kind == "binary":
        url, expected = tool["url"], tool["sha256"].lower()
        if not url.startswith("https://"):
            return f"{tool['name']}: refused (not https)"
        with urllib.request.urlopen(url, timeout=120) as r:  # https only  # nosec B310
            data = r.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            return f"{tool['name']}: REFUSED, the download's fingerprint doesn't match the registry ({actual[:12]}…)"
        BIN.mkdir(parents=True, exist_ok=True)
        target = BIN / Path(tool.get("extract") or url).name
        member = tool.get("extract")
        if member and url.endswith((".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                target.write_bytes(tar.extractfile(member).read())
        elif member and url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                target.write_bytes(z.read(member))
        else:
            target.write_bytes(data)
        target.chmod(0o755)
        return f"{tool['name']}: installed ({target})"
    return f"{tool['name']}: unknown kind {kind}"


def main(names: list[str]) -> int:
    tools = {norm(t["name"]): t for t in load_registry().get("tools", [])}
    status = 0
    for name in names:
        tool = tools.get(norm(name))
        if not tool:
            print(f"{name}: not in the registry (find_tools lists what is)")
            status = 1
            continue
        try:
            line = install(tool)
        except Exception as e:
            line, status = f"{name}: FAILED ({type(e).__name__}: {e})", 1
        print(line)
        status = status or ("FAILED" in line or "REFUSED" in line)
    return int(bool(status))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
