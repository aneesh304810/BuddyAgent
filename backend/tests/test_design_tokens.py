"""The design tokens the app and the server pages use match their one source (buddy-redesign's build_tokens.py)."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "buddy-redesign/06-design-system/build_tokens.py"


def _tokens():
    spec = importlib.util.spec_from_file_location("build_tokens", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_server_pages_and_app_carry_the_generated_tokens():
    t = _tokens()
    assert t.WEB_CSS.read_text() == t.css(), "run python3 buddy-redesign/06-design-system/build_tokens.py"
    assert t.APP_KT.read_text() == t.kotlin(), "run python3 buddy-redesign/06-design-system/build_tokens.py"


def test_every_text_colour_meets_contrast():
    t = _tokens()
    for theme in ("light", "dark"):
        c = t.COLOURS[theme]
        for fg, bg in t.TEXT_PAIRS:
            assert t.ratio(c[fg], c[bg]) >= 4.5, (theme, fg, bg)
        for fg, bg in t.UI_PAIRS:
            assert t.ratio(c[fg], c[bg]) >= 3, (theme, fg, bg)


def test_both_themes_name_the_same_colours():
    t = _tokens()
    assert list(t.COLOURS["light"]) == list(t.COLOURS["dark"])


def test_every_server_page_takes_its_colours_from_the_tokens():
    pages = sorted((REPO / "web").glob("*.html"))
    assert len(pages) >= 14
    shared = ("--bg:", "--card:", "--text:", "--muted:", "--accent:", "--line:")
    for page in pages:
        html = page.read_text()
        head = html.split("<style>", 1)[0] if "<style>" in html else html.split("</head>", 1)[0]
        assert 'href="tokens.css"' in head, page.name
        if page.name in ("index.html", "glasses.html"):  # always the dark look (the chat shell; the lens preview)
            assert '<html lang="en" data-theme="dark">' in html, page.name
            continue
        assert 'href="theme.css"' in head and '<script src="theme.js"></script>' in head, page.name
        assert not any(name in html for name in shared), f"{page.name} defines its own shared colours"


def test_theme_css_names_only_tokens():
    css = (REPO / "web" / "theme.css").read_text()
    values = [d.split(":", 1)[1].strip() for d in css.split("{", 1)[1].split("}")[0].split(";") if ":" in d]
    assert values and all(v.startswith("var(--b-") for v in values)
