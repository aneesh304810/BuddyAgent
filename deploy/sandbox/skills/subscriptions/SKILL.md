---
name: subscriptions
description: "Transactions (CSV) -> every recurring charge with its usual amount and monthly cost, price increases, duplicate charges and the yearly total of all subscriptions."
tags: [subscriptions, recurring, money, spending, price increase, duplicate, bank, card]
data: true
run: "python /buddy/skills/subscriptions/subscriptions.py {input} {args}"
---
# subscriptions

    python /buddy/skills/subscriptions/subscriptions.py transactions.csv

A merchant charged in 2+ different months with similar amounts (within 25%) is a subscription.
Prints JSON: each subscription (usual and latest amount, how often, last charged, yearly cost),
price increases (latest vs the one before), duplicates (same merchant and amount within 3 days)
and the monthly and yearly totals. Spending is the negative amounts when there are both signs.
