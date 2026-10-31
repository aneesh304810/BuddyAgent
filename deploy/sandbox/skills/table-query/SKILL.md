---
name: table-query
description: "A spreadsheet (CSV or Excel) -> answers by code: filter rows, group and total, sort, top N; or a quick look at its columns. Exact numbers without reading every cell."
tags: [csv, excel, xlsx, spreadsheet, table, filter, group, sum, average, pivot, data]
data: true
run: "python /buddy/skills/table-query/table_query.py {input} {args}"
---
# table-query

    python /buddy/skills/table-query/table_query.py data.csv                         # columns, types, first rows
    python /buddy/skills/table-query/table_query.py data.xlsx --spec '{"where": [["country", "==", "Peru"]],
        "group_by": ["city"], "agg": {"price": "mean", "id": "count"}, "sort": ["-price"], "limit": 10}'

`where` operators: == != > >= < <= contains. `agg`: sum, mean, min, max, count, median.
Prints JSON rows (at most 200).
