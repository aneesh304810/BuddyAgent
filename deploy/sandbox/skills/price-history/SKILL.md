---
name: price-history
description: "Prices seen over time for something (a flight route, a product, a hotel) as CSV -> lowest, highest, typical, the latest and where it sits, the trend, and a plain buy-now-or-wait signal."
tags: [price, history, deals, flights, trend, buy, wait, watch, shopping]
data: true
run: "python /buddy/skills/price-history/price_history.py {input} {args}"
---
# price-history

    python /buddy/skills/price-history/price_history.py prices.csv [--target 300]

Input CSV columns: seen_at, price (and optionally currency, where). Prints JSON: how many
prices over how many days, lowest (when, where), highest, median, the latest and its percentile
(0 = cheapest ever seen), the trend per week, and a signal: "good price" (latest in the cheapest
20%, or at/below --target), "rising" (buy soon), "falling" (wait) or "typical".
