---
name: compare-offers
description: "Offers (flights, hotels, products) as JSON -> a comparison by TRUE total (price plus every fee), sorted, with the cheapest, the best refundable one and what each catch costs."
tags: [compare, deals, flights, hotels, price, fees, total, offers, shopping, booking]
data: true
run: "python /buddy/skills/compare-offers/compare.py {input} {args}"
---
# compare-offers

    python /buddy/skills/compare-offers/compare.py offers.json [--csv output/compare.csv]

Input: a JSON list of offers like {"name": "LATAM via Bogota", "price": 289, "currency": "USD",
"fees": {"checked bag": 45, "seat": 15}, "refundable": false, "link": "..."}. Any other numeric
field ending in _fee counts as a fee too. Prints JSON: offers sorted by true total, the cheapest,
the cheapest refundable, and how much the fees added.
