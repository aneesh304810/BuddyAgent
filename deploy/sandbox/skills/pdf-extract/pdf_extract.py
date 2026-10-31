#!/usr/bin/env python3
"""PDF -> text and keyword passages as JSON (pypdf). No network, no AI."""
import argparse
import json
import re

from pypdf import PdfReader


def pages_wanted(spec: str, count: int) -> list[int]:
    if not spec:
        return list(range(count))
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        out += [i - 1 for i in range(lo, hi + 1) if 1 <= i <= count]
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--find", default="")
    p.add_argument("--pages", default="")
    p.add_argument("--max-chars", type=int, default=6000)
    a = p.parse_args()
    reader = PdfReader(a.input)
    texts = [(page.extract_text() or "") for page in reader.pages]
    result = {"pages": len(texts), "characters": sum(map(len, texts))}
    if not result["characters"]:
        result["note"] = "No text layer: this looks like a scanned PDF."
    if a.find:
        words = [w.strip() for w in a.find.split(",") if w.strip()]
        hits = []
        for i, text in enumerate(texts):
            flat = re.sub(r"\s+", " ", text)
            for w in words:
                for m in re.finditer(re.escape(w), flat, re.I):
                    hits.append({"page": i + 1, "word": w, "text": flat[max(0, m.start() - 300):m.end() + 300]})
        result["passages"] = hits[:30]
    else:
        joined = "\n\n".join(f"[page {i + 1}]\n{texts[i]}" for i in pages_wanted(a.pages, len(texts)))
        result["text"] = joined[: a.max_chars]
        result["cut"] = len(joined) > a.max_chars
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
