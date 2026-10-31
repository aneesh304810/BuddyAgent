---
name: doc-snippets
description: "A long text (an article, a transcript, notes, an extracted PDF) and a question -> only the passages that matter, best first, so the AI reads a few hundred words instead of everything."
tags: [text, document, search, passages, summary, transcript, article, long, question]
data: true
run: "python /buddy/skills/doc-snippets/snippets.py {input} {args}"
---
# doc-snippets

    python /buddy/skills/doc-snippets/snippets.py article.txt --question "what are the cancellation fees" [--top 6]

Splits the text into passages of about 600 characters and ranks them by the question's words
(BM25-style scoring). Prints JSON: the top passages with their position and score.
