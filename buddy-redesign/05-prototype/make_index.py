"""Wraps prototype.html (written for the published page, which adds its own document shell) into index.html, a
standalone page to open from disk.  python3 buddy-redesign/05-prototype/make_index.py"""
from pathlib import Path
here = Path(__file__).parent
body = (here / "prototype.html").read_text()
(here / "index.html").write_text('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                                 '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                                 '</head><body>\n' + body + '\n</body></html>\n')
print("index.html written")
