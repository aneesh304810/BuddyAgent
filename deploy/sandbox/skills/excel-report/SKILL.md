---
name: excel-report
description: Turn a CSV or table into a polished Excel report with totals per group and a native chart.
tags: [excel, xlsx, spreadsheet, report, totals, summary, chart, csv]
---
# excel-report

    python /buddy/skills/excel-report/excel_report.py DATA.csv output/Report.xlsx \
        --title "Spending, October" --group-by category --sum amount --chart bar

Makes two sheets: **Summary** (totals per group, sorted, with a bar or pie chart) and **Data**
(the full table with filters and frozen headers). Without `--group-by` it writes the data sheet
only. Works with `.csv` or `.xlsx` input.
