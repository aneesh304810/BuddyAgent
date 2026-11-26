"""Builds every Buddy logo and icon file from one set of shapes, so they never drift apart.

    python3 buddy-redesign/01-brand/build_logos.py

Writes SVG logos (three concepts, the chosen lockups, the mark, mono versions), the adaptive Android icon layers
(SVG and VectorDrawable XML), a small mark and the favicon. All shapes are round-capped strokes on a 96-unit grid:
no fonts are needed anywhere, so the files render the same in every tool.
"""

from pathlib import Path

HERE = Path(__file__).parent
LOGOS, ICONS = HERE / "logos", HERE / "icons"

GREEN, GREEN_DARK, INK, PAPER = "#1F6F5C", "#174E41", "#17211D", "#FFFFFF"
GREEN_ON_DARK, INK_ON_DARK, BG_DARK = "#75B99A", "#F2F5F1", "#101713"

# --- the shapes -------------------------------------------------------------------------------------------------
# The chosen mark: the wordmark's "b" with a short tail at the foot of its stem, so the letter also reads as a
# speech bubble (the conversation is the product). 96 x 96 grid, stroke 10.
MARK = ('<path d="M30 14V70"/><circle cx="50" cy="56" r="20"/><path d="M30 70L21 83"/>')
MARK_STROKE = 10

# Concept 1: a premium "B" monogram, monoline, on a rounded tile.
MONOGRAM = ('<path d="M30 18V78"/><path d="M30 18H50a14 14 0 0 1 0 28H30"/>'
            '<path d="M30 46H54a16 16 0 0 1 0 32H30"/>')

# Concept 2: connected intelligence. A "b" drawn as a small network: a stem, a ring and three nodes.
CONNECTED_LINES = '<path d="M30 16V58"/><circle cx="50" cy="58" r="20"/><path d="M30 16L50 38"/>'
CONNECTED_NODES = [(30, 16), (50, 38), (70, 58)]

# Concept 3 (and the chosen wordmark): "buddy", monoline lowercase on a baseline at 64, x-height 32, stroke 8.
WORDMARK = ('<path d="M10 12V64"/><circle cx="26" cy="48" r="16"/>'
            '<path d="M54 32V48a14 14 0 0 0 28 0M82 32V64"/>'
            '<circle cx="110" cy="48" r="16"/><path d="M126 12V64"/>'
            '<circle cx="154" cy="48" r="16"/><path d="M170 12V64"/>'
            '<path d="M182 32V48a14 14 0 0 0 28 0M210 32V70a14 14 0 0 1 -14 14H190"/>')
WORD_STROKE = 8


def stroke_group(body: str, color: str, width: float) -> str:
    return (f'<g fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</g>')


def svg(w: float, h: float, body: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" role="img" aria-label="{title}">'
            f'<title>{title}</title>{body}</svg>\n')


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build() -> list[str]:
    out = []

    def put(path: Path, text: str):
        write(path, text)
        out.append(str(path.relative_to(HERE)))

    # concepts
    put(LOGOS / "concept-1-monogram.svg", svg(96, 96, f'<rect width="96" height="96" rx="24" fill="{GREEN}"/>'
        + stroke_group(MONOGRAM, PAPER, 10), "Buddy concept 1: B monogram"))
    nodes = "".join(f'<circle cx="{x}" cy="{y}" r="7" fill="{GREEN}"/>' for x, y in CONNECTED_NODES)
    put(LOGOS / "concept-2-connected.svg", svg(96, 96, stroke_group(CONNECTED_LINES, INK, 6) + nodes,
        "Buddy concept 2: connected intelligence"))
    put(LOGOS / "concept-3-wordmark.svg", svg(220, 92, stroke_group(WORDMARK, INK, WORD_STROKE),
        "Buddy concept 3: wordmark"))

    # the chosen system: mark + wordmark
    put(LOGOS / "buddy-mark.svg", svg(96, 96, stroke_group(MARK, GREEN, MARK_STROKE), "Buddy"))
    put(LOGOS / "buddy-mark-on-dark.svg", svg(96, 96, stroke_group(MARK, GREEN_ON_DARK, MARK_STROKE), "Buddy"))
    lockup = (stroke_group(MARK, GREEN, MARK_STROKE)
              + f'<g transform="translate(108 2)">{stroke_group(WORDMARK, INK, WORD_STROKE)}</g>')
    put(LOGOS / "buddy-logo-primary.svg", svg(336, 96, lockup, "Buddy"))
    lockup_dark = (stroke_group(MARK, GREEN_ON_DARK, MARK_STROKE)
                   + f'<g transform="translate(108 2)">{stroke_group(WORDMARK, INK_ON_DARK, WORD_STROKE)}</g>')
    put(LOGOS / "buddy-logo-primary-on-dark.svg", svg(336, 96, lockup_dark, "Buddy"))
    stacked = (f'<g transform="translate(62 0)">{stroke_group(MARK, GREEN, MARK_STROKE)}</g>'
               f'<g transform="translate(0 104)">{stroke_group(WORDMARK, INK, WORD_STROKE)}</g>')
    put(LOGOS / "buddy-logo-secondary-stacked.svg", svg(220, 196, stacked, "Buddy"))
    mono = (stroke_group(MARK, "currentColor", MARK_STROKE)
            + f'<g transform="translate(108 2)">{stroke_group(WORDMARK, "currentColor", WORD_STROKE)}</g>')
    put(LOGOS / "buddy-logo-mono.svg", svg(336, 96, mono, "Buddy"))
    put(LOGOS / "buddy-mark-mono.svg", svg(96, 96, stroke_group(MARK, "currentColor", MARK_STROKE), "Buddy"))

    # Android adaptive icon (108 dp canvas, 72 dp safe zone; the mark sits in the middle 60 dp)
    scale, dx, dy = 0.74, 54 - 45 * 0.74, 54 - 49 * 0.74  # centre the mark's visual middle (45, 49) at (54, 54)
    fg = f'<g transform="translate({dx:.2f} {dy:.2f}) scale({scale})">{stroke_group(MARK, PAPER, MARK_STROKE)}</g>'
    put(ICONS / "app-icon-foreground.svg", svg(108, 108, fg, "Buddy icon foreground"))
    put(ICONS / "app-icon-background.svg", svg(108, 108, f'<rect width="108" height="108" fill="{GREEN}"/>',
        "Buddy icon background"))
    mono_fg = f'<g transform="translate({dx:.2f} {dy:.2f}) scale({scale})">{stroke_group(MARK, "#000000", MARK_STROKE)}</g>'
    put(ICONS / "app-icon-monochrome.svg", svg(108, 108, mono_fg, "Buddy icon monochrome"))
    preview = (f'<defs><clipPath id="c"><circle cx="54" cy="54" r="54"/></clipPath></defs>'
               f'<g clip-path="url(#c)"><rect width="108" height="108" fill="{GREEN}"/>{fg}</g>')
    put(ICONS / "app-icon-preview-circle.svg", svg(108, 108, preview, "Buddy icon"))
    squircle = (f'<rect width="108" height="108" rx="30" fill="{GREEN}"/>{fg}')
    put(ICONS / "app-icon-preview-squircle.svg", svg(108, 108, squircle, "Buddy icon"))

    # VectorDrawable for Android (same geometry; arcs written out for the circle)
    def vd_paths(color: str) -> str:
        w = MARK_STROKE
        common = (f'android:strokeColor="{color}" android:strokeWidth="{w}" android:strokeLineCap="round" '
                  f'android:strokeLineJoin="round" android:fillColor="#00000000"')
        return (f'        <path {common} android:pathData="M30,14 L30,70"/>\n'
                f'        <path {common} android:pathData="M70,56 A20,20 0 1,1 30,56 A20,20 0 1,1 70,56"/>\n'
                f'        <path {common} android:pathData="M30,70 L21,83"/>\n')
    vd = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<!-- Buddy mark (the "b" with a speech tail), white, for the adaptive icon foreground. Generated by\n'
          '     buddy-redesign/01-brand/build_logos.py: edit the shapes there, not here. -->\n'
          '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
          '    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">\n'
          f'    <group android:translateX="{dx:.2f}" android:translateY="{dy:.2f}" android:scaleX="{scale}" android:scaleY="{scale}">\n'
          + vd_paths("#FFFFFFFF") + '    </group>\n</vector>\n')
    put(ICONS / "ic_launcher_foreground.xml", vd)

    # small mark (24 px) and favicon
    put(ICONS / "small-mark-24.svg", svg(96, 96, stroke_group(MARK, GREEN, 12), "Buddy"))
    fav = (f'<style>.t{{fill:{GREEN}}}@media (prefers-color-scheme: dark){{.t{{fill:{GREEN_ON_DARK}}}}}</style>'
           f'<rect class="t" width="96" height="96" rx="22"/>'
           f'<g transform="translate(9.6 9.6) scale(0.8)">{stroke_group(MARK, PAPER, 12)}</g>')
    put(ICONS / "favicon.svg", svg(96, 96, fav, "Buddy"))
    return out


if __name__ == "__main__":
    for f in build():
        print(f)
