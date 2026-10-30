#!/usr/bin/env python3
"""Simple Markdown -> a clean A4 PDF with reportlab."""
import argparse
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("markdown")
    p.add_argument("out")
    p.add_argument("--title", default="")
    a = p.parse_args()
    styles = getSampleStyleSheet()
    story = [Paragraph(inline(a.title), styles["Title"]), Spacer(1, 0.3 * cm)] if a.title else []
    bullets, table = [], []

    def flush():
        nonlocal bullets, table
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(inline(b), styles["BodyText"])) for b in bullets],
                                      bulletType="bullet", leftIndent=12))
            bullets = []
        if table:
            rows = [r for r in table if not re.fullmatch(r"[\s|:-]+", "|".join(r))]
            t = Table([[Paragraph(inline(c), styles["BodyText"]) for c in r] for r in rows], repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4C78DB")),
                                   ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                   ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9CED6")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([t, Spacer(1, 0.3 * cm)])
            table = []

    for line in Path(a.markdown).read_text().splitlines():
        s = line.strip()
        if s.startswith("|"):
            if bullets:
                flush()
            table.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if table:
            flush()
        if re.match(r"^[-*] ", s):
            bullets.append(s[2:])
            continue
        flush()
        image = re.match(r"!\[(.*?)\]\((.+?)\)", s)
        if image and Path(image.group(2)).exists():
            img = Image(image.group(2))
            scale = min(1.0, (16 * cm) / img.drawWidth)
            img.drawWidth, img.drawHeight = img.drawWidth * scale, img.drawHeight * scale
            story.extend([img, Paragraph(f"<i>{inline(image.group(1))}</i>", styles["BodyText"])])
        elif s.startswith("#"):
            level = min(len(s) - len(s.lstrip("#")), 3)
            story.append(Paragraph(inline(s.lstrip("# ")), styles[f"Heading{level}"]))
        elif s:
            story.append(Paragraph(inline(s), styles["BodyText"]))
        else:
            story.append(Spacer(1, 0.2 * cm))
    flush()
    SimpleDocTemplate(a.out, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm, title=a.title).build(story)
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
