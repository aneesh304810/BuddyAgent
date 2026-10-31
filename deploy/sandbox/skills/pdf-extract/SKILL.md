---
name: pdf-extract
description: "PDF -> its text, page count, and the passages around chosen words (a contract's cancellation clause, a statement's totals), so only the parts that matter go to the AI."
tags: [pdf, extract, text, contract, statement, document, search, pages]
data: true
run: "python /buddy/skills/pdf-extract/pdf_extract.py {input} {args}"
---
# pdf-extract

    python /buddy/skills/pdf-extract/pdf_extract.py file.pdf [--find "cancel,refund,fee"] [--pages 1-3] [--max-chars 6000]

Prints JSON: pages, characters, and either the passages around each word in `--find` (with
page numbers) or the text of `--pages` (cut at `--max-chars`). Scanned PDFs without a text layer
come out empty: say so rather than guessing.
