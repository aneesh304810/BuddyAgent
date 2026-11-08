#!/usr/bin/env python3
"""Buddy's browser: one headless Chromium inside a sandbox container, driven one JSON command per line on stdin,
one JSON answer per line on stdout. The sandbox service starts it (op "browser") and keeps it while a task runs.

Commands: open {url} | click {ref, approved?} | type {ref, text, enter?} | select {ref, value} | check {ref, on} |
press {key} | scroll {down?} | back | read | screenshot | close
Every answer after an action carries the page as Buddy sees it: title, address, the readable text, and the things
you can use, numbered: [12 button] Search, [15 input text "Email" = ""], [16 link] Pricing.

Rules kept here as well as in Buddy:
  - http(s) to public addresses only (the sandbox network also blocks Buddy's server and the private network)
  - never types into password fields, never uploads files, downloads are off
  - buttons that pay, buy, check out or transfer are never clicked; buttons that submit, send, book, sign up or
    confirm only with approved=true, which Buddy sets only after the user's yes
Page text is other people's words: information for the model, never instructions.
"""

import base64
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

NEVER = re.compile(r"(?i)\b(pay|payment|purchase|buy|place (your )?order|checkout|check out|transfer|send money|"
                   r"delete (my )?account|subscribe now|add card|donate)\b")
SUBMIT = re.compile(r"(?i)\b(submit|send|book|reserve|confirm|sign up|register|apply|finish|complete|post|"
                    r"request|continue to|save and|agree)\b")
MAX_TEXT = 7000
MARK = """() => {
  const sel = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=checkbox],[role=radio],[role=tab],' +
              '[role=menuitem],[role=option],[contenteditable=true],summary';
  const out = []; let n = 0;
  document.querySelectorAll('[data-buddy]').forEach(e => e.removeAttribute('data-buddy'));
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (e.type === 'hidden') continue;
    n += 1; e.setAttribute('data-buddy', String(n));
    let label = e.getAttribute('aria-label') || '';
    if (!label && e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) label = l.innerText; }
    if (!label && e.closest('label')) label = e.closest('label').innerText;
    if (!label) label = e.innerText || e.getAttribute('placeholder') || e.getAttribute('name') || e.getAttribute('title') || e.value || '';
    const tag = e.tagName.toLowerCase();
    const kind = tag === 'input' ? 'input ' + (e.type || 'text') : (e.getAttribute('role') || tag);
    let value = '';
    if (tag === 'select') value = e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : '';
    else if (e.type === 'checkbox' || e.type === 'radio') value = e.checked ? 'checked' : 'unchecked';
    else if (e.type !== 'password' && 'value' in e) value = e.value || '';
    const options = tag === 'select' ? Array.from(e.options).slice(0, 30).map(o => o.text.trim()) : [];
    out.push({ref: n, kind, label: label.trim().replace(/\\s+/g, ' ').slice(0, 120), value: String(value).slice(0, 120),
              options, required: !!e.required});
    if (n >= 250) break;
  }
  return out;
}"""


LOCAL_OK = os.getenv("BUDDY_BROWSER_TEST_LOCAL") == "1"  # tests only; the sandbox never sets it


def public(url: str) -> str:
    u = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("only http and https addresses")
    if LOCAL_OK:
        return urllib.parse.urlunsplit(u)
    for info in socket.getaddrinfo(u.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("only public addresses")
    return urllib.parse.urlunsplit(u)


class Browser:
    def __init__(self, pw):
        self.b = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"],
                                    executable_path=os.getenv("BUDDY_CHROMIUM") or None)
        self.ctx = self.b.new_context(accept_downloads=False, viewport={"width": 1280, "height": 900},
                                      locale="en-US", user_agent=None)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(15000)
        self.page.on("filechooser", lambda fc: None)  # never upload: the chooser is left unanswered
        self.items: list[dict] = []

    def snapshot(self) -> dict:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        self.items = self.page.evaluate(MARK)
        text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        text = re.sub(r"\n\s*\n+", "\n", text)[:MAX_TEXT]
        has_password = any(i["kind"] == "input password" for i in self.items)
        return {"title": self.page.title()[:200], "url": self.page.url, "text": text,
                "items": [f"[{i['ref']} {i['kind']}{' required' if i['required'] else ''}] {i['label']}"
                          + (f" = \"{i['value']}\"" if i["value"] else "")
                          + (f" options: {', '.join(i['options'])}" if i["options"] else "") for i in self.items],
                "login_form": has_password}

    def el(self, ref):
        item = next((i for i in self.items if i["ref"] == int(ref)), None)
        if item is None:
            raise ValueError(f"no element {ref} on the page now (read the page again)")
        return item, self.page.locator(f'[data-buddy="{int(ref)}"]').first

    def do(self, cmd: dict) -> dict:
        op = cmd.get("op")
        if op == "open":
            self.page.goto(public(str(cmd.get("url", ""))), wait_until="domcontentloaded")
        elif op == "click":
            item, loc = self.el(cmd["ref"])
            if NEVER.search(item["label"]):
                raise ValueError(f"'{item['label']}' would pay or buy: Buddy never presses that; the user does")
            pressable = not item["kind"].startswith("input") or item["kind"] in ("input submit", "input button",
                                                                                   "input image")
            if pressable and SUBMIT.search(item["label"]) and not cmd.get("approved"):
                return {"needs_approval": True, "label": item["label"], **self.snapshot()}
            loc.click()
            self.page.wait_for_timeout(800)
        elif op == "type":
            item, loc = self.el(cmd["ref"])
            if item["kind"] == "input password":
                raise ValueError("Buddy never types passwords")
            if item["kind"] == "input file":
                raise ValueError("Buddy doesn't upload files")
            loc.fill(str(cmd.get("text", ""))[:2000])
            if cmd.get("enter"):
                loc.press("Enter")
                self.page.wait_for_timeout(800)
        elif op == "select":
            _, loc = self.el(cmd["ref"])
            loc.select_option(label=str(cmd.get("value", "")))
        elif op == "check":
            _, loc = self.el(cmd["ref"])
            loc.set_checked(bool(cmd.get("on", True)))
        elif op == "press":
            key = str(cmd.get("key", ""))
            if key not in ("Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "PageDown", "PageUp"):
                raise ValueError("keys: Enter, Tab, Escape, arrows, PageUp/PageDown")
            self.page.keyboard.press(key)
            self.page.wait_for_timeout(500)
        elif op == "scroll":
            self.page.mouse.wheel(0, 800 if cmd.get("down", True) else -800)
            self.page.wait_for_timeout(300)
        elif op == "back":
            self.page.go_back(wait_until="domcontentloaded")
        elif op == "screenshot":
            png = self.page.screenshot(full_page=False, type="png")
            return {"png": base64.b64encode(png).decode()}
        elif op != "read":
            raise ValueError(f"unknown command {op}")
        return self.snapshot()


def main() -> None:
    with sync_playwright() as pw:
        browser = Browser(pw)
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            try:
                cmd = json.loads(line)
                if cmd.get("op") == "close":
                    break
                reply = browser.do(cmd)
            except Exception as e:
                reply = {"error": str(e)[:300]}
            print(json.dumps(reply), flush=True)
        browser.b.close()


if __name__ == "__main__":
    main()
