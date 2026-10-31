---
name: spending-summary
description: "Bank or card transactions (CSV or JSON) -> totals by category, month and merchant, the biggest charges, recurring payments and unusual ones. Exact numbers, computed by code."
tags: [money, spending, transactions, bank, card, budget, statement, csv, data]
data: true
run: "python /buddy/skills/spending-summary/spending.py {input} {args}"
---
# spending-summary

    python /buddy/skills/spending-summary/spending.py transactions.csv [--currency USD]

Finds the date, amount, merchant (or description) and category columns by name. With both
negative and positive amounts, the negative ones are spending; with only positive ones, all are.
Prints JSON: period, total spent, money in, by category, by month, top merchants, the five
largest charges, recurring payments (same merchant in 3+ months) and unusual charges (over 3x the
merchant's usual amount).
