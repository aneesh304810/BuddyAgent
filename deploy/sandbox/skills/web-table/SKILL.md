---
name: web-table
description: Pull the tables from a web page into CSV (prices, rankings, schedules, statistics).
tags: [web, scrape, table, html, csv, prices, data]
---
# web-table

    python /buddy/skills/web-table/web_table.py "https://example.com/page" output/table.csv --index 0

Lists the tables it found (with their columns) and saves the one you pick with `--index`
(or `--all` to save each as `table-N.csv` next to the output). Respect sites' terms; it sends a
normal browser user agent and fetches one page.
