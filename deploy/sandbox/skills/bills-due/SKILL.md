---
name: bills-due
description: "Emails -> the bills in them with amount, currency and due date (credit cards, utilities, phone, insurance, rent, tax), soonest first, with the total due in the next 30 days."
tags: [bills, due, payment, invoice, statement, utilities, credit card, money, email]
data: true
run: "python /buddy/skills/bills-due/bills.py {input} {args}"
---
# bills-due

    python /buddy/skills/bills-due/bills.py emails.json [--dmy]

Input: a JSON list of {date, from, subject, text}. A message is a bill when it talks about an
amount due, minimum payment, statement, invoice or autopay. Prints JSON: bills (who, amount,
currency, due date, days left, autopay or not), overdue, and totals due in 7 and 30 days.
