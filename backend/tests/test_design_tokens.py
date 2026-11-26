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
