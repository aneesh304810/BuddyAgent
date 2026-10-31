---
name: receipts-extract
description: "Emails or messages (JSON list or plain text) -> a table of receipts, bookings, bills and refunds: date, from, amount and currency, order or booking number, kind."
tags: [email, receipt, booking, bill, invoice, refund, order, extract, money, travel]
data: true
run: "python /buddy/skills/receipts-extract/receipts.py {input} {args}"
---
# receipts-extract

    python /buddy/skills/receipts-extract/receipts.py emails.json [--csv output/receipts.csv]

Input: a JSON list of {date, from, subject, text}, or plain text with messages separated by a
line of dashes. Finds amounts with their currency ($, ₹, €, £, R$, S/, codes), order / booking /
confirmation numbers, and whether it's a receipt, booking, bill or refund. Prints JSON rows.
