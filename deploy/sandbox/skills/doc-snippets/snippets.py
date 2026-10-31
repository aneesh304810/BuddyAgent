#!/usr/bin/env python3
"""Long text + question -> the most relevant passages as JSON (BM25-style). No network, no AI."""
import argparse
import json
import math
import re
from collections import Counter

STOP = set("the a an and or of to in on for is are was were be it this that with as at by from what how why when "
           "which who do does did i you we they he she my your our their there here can could would should".split())


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[\w']+", text.lower()) if w not in STOP and len(w) > 1]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("--question", required=True)
    p.add_argument("--top", type=int, default=6)
    p.add_argument("--size", type=int, default=600)
    a = p.parse_args()
    text = re.sub(r"\s+", " ", open(a.input, encoding="utf-8", errors="replace").read())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) > a.size and current:
            chunks.append(current.strip())
            current = ""
        current += s + " "
    if current.strip():
        chunks.append(current.strip())
    docs = [Counter(words(c)) for c in chunks]
    avg = sum(sum(d.values()) for d in docs) / max(1, len(docs))
    query = set(words(a.question))
    df = {w: sum(1 for d in docs if w in d) for w in query}
    scored = []
    for i, d in enumerate(docs):
        length = sum(d.values()) or 1
        score = sum(math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5)) * d[w] * 2.2 /
                    (d[w] + 1.2 * (0.25 + 0.75 * length / avg)) for w in query if d[w])
        if score > 0:
            scored.append((score, i))
    best = sorted(scored, reverse=True)[: a.top]
    print(json.dumps({"passages": [{"position": i + 1, "of": len(chunks), "score": round(s, 2), "text": chunks[i]}
                                   for s, i in best], "total_characters": len(text)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
