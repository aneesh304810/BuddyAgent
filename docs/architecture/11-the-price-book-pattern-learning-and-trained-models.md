# The price book, pattern learning and trained models

A separate market-price database, nightly pattern learning in the sandbox, and models trained on judged calls once there is enough data.

## Architecture

```mermaid
flowchart LR
    you(["You: voice, chat, the app"])
    subgraph Tools["What Buddy can do"]
        t_price_book["price_book"]
    end
    subgraph Modules["Modules"]
        pricebook_py["pricebook.py<br/>Buddy's separate database of market prices and deals (DuckDB), for…"]
        pricestore_py["pricestore.py<br/>Daily stock prices, kept"]
        pricecheck_py["pricecheck.py<br/>A second opinion on the kept daily prices"]
        productid_py["productid.py<br/>which product a deal headline is about, its brand and its category"]
        patterns_py["patterns.py<br/>Nightly pattern learning in the sandbox: Buddy hands the price book…"]
        modeltrain_py["modeltrain.py<br/>Trained models, when there is enough to learn from: each week Buddy…"]
    end
    you --> t_price_book
    t_price_book --> pricebook_py
    pricebook_py --> patterns_py
    pricebook_py --> productid_py
    pricestore_py --> pricebook_py
    pricecheck_py --> pricebook_py
    pricecheck_py --> pricestore_py
    patterns_py --> pricebook_py
    modeltrain_py --> pricebook_py
    db[("Your database<br/>daily_prices, deals, launches, meta, models, observations, pattern_months, predictions and more")]
    pricebook_py --> db
    pricestore_py --> db
    pricecheck_py --> db
    modeltrain_py --> db
    s_tiingo["Tiingo (price cross-check)<br/>(public data only)"]
    pricecheck_py --> s_tiingo
    o_0["Buddy Connect"]
    pricebook_py -. uses .-> o_0
    o_1["Buddy Intelligence Platform"]
    pricebook_py -. uses .-> o_1
    o_2["Claude"]
    pricebook_py -. uses .-> o_2
    o_3["Fixing and explaining itself"]
    pricebook_py -. uses .-> o_3
    o_4["Investing"]
    pricebook_py -. uses .-> o_4
    o_5["Memory"]
    pricebook_py -. uses .-> o_5
```

The diagram reads left to right: what you ask, the tools Buddy uses, the modules that do the work, and what they keep or call. Dashed arrows are other features this one relies on.

## What Buddy can do here

| Tool | What it is for | Actions | Module |
|---|---|---|---|
| `price_book` | Buddy's separate price database (market data only: products, flights, hotels, deals, and the buy-or-wait calls with outcomes) | stats, history, export, patterns, learn | pricebook.py |

## Technical design, module by module

### pricebook.py

```text
The price book: Buddy's separate database of market prices and deals (DuckDB), for history and predictions.

Raw prices and deals are kept a year, then deleted once the nightly learning in the sandbox (patterns.py) has
learned from them; the learned patterns (pattern_months), launches and predictions are kept for good.

What's in it (no personal data: products, routes, hotels, prices and dates):
  observations  every price Buddy sees: products (store, regular price, on sale), flights (route, travel date,
                airline, stops) and hotels (city or hotel, check-in, nights), with when and where it was seen
  deals         every deal read from the deal feeds (title, price, store, link, when)
  predictions   Buddy's buy-now-or-wait calls (the item, its price, the price worth waiting for, the date) and how
                each turned out, to measure and improve the calls

What stays in Buddy's encrypted personal database: what the owner wants, considers, bought and thinks of it, their
profile and price watches. Those only point into the price book by the item's name.

One file next to Buddy's database (pricebook.duckdb, or BUDDY_PRICEBOOK), not encrypted: it holds only market data,
so it can be copied, exported (CSV or Parquet) and analysed with any tool. Note that the routes, dates and hotels
Buddy looked up do show which trips were searched. DuckDB is a columnar file built for fast history queries; it is
kept small in memory (256 MB cap, one thread) for Buddy's server. The nightly backup includes it.

Older history from Buddy's personal database (price_seen, deals, buy_predictions, travel_prices) is copied in once.

When the engine fails. DuckDB has twice stopped on this price book with an internal error of its own ("Attempted to
dereference shared_ptr that is NULL"), and after one it refuses every statement until the file is opened again.
So run() opens the price book again and asks once more; if the same statement fails the same way, it rebuilds the
file once (rebuild(): the engine's own export and import, into a new file, the old one kept beside it) and asks a
last time. check() says what can be read; deploy/pricebook.sh runs both by hand. The tables have no secondary
index: every internal error so far involved a table that had one, and the price book is small enough without.
```

- Tables: `deals`, `launches`, `meta`, `observations`, `pattern_months`, `predictions`
- Settings: `BUDDY_PRICEBOOK`
- Uses: patterns.py, productid.py, tools.py

### pricestore.py

```text
Daily stock prices, kept. Every daily price Buddy fetches for the strategy lab is stored in the price book, so
the history does not depend on a provider's free plan, a test can be repeated, and a replay does not download
five years of prices every time.

  keep     each symbol's daily open, high, low, close and volume, with the source it came from and when it was
           fetched. Market data only: nothing about the owner.
  fetch    what the lab calls in place of asking the provider directly. It asks the provider only for what the
           store lacks (the days since the last one held, or the whole window for a symbol it has never seen), keeps
           it, and returns the bars from the store.
  revised  a provider re-states past prices after a share split. When a newly fetched price for a day already held
           differs by more than half a percent, that symbol's whole history is fetched again and replaced, and the
           change is written down (price_revisions), so it is known that an earlier test used different numbers.
           The newest day held is never compared: it may have been fetched before the market closed.

What this is not: a record of companies that no longer trade, or of which companies were in an index on a past
date. It keeps what Buddy has asked for, from the day it first asked. One source at a time: prices from different
providers are kept apart and never mixed in one series.
```

- Tables: `daily_prices`, `price_revisions`
- Uses: memory.py, pricebook.py

### pricecheck.py

```text
A second opinion on the kept daily prices. With one source, a wrong price is kept as it stands and every test
built on it is wrong too. This compares a few symbols a day against a second, independent provider (Tiingo, on
its free plan) and says where the two disagree.

  off      until the owner sets BUDDY_TIINGO_KEY. Nothing is sent and nothing is claimed.
  each day CHECK_A_DAY symbols from the price store, the ones checked longest ago first, so the whole list is
           gone round in a week or so. One request a symbol, well inside the free plan's hourly limit.
  compare  the last WINDOW_DAYS of closes, day by day, against the second source's unadjusted close. A day that
           differs by more than DIFFERS is a disagreement. The newest day held is left out (it may have been
           fetched before the close), and a symbol whose shares split inside the window is not compared (one
           source adjusts for the split and the other reports the price as it was).
  record   per symbol: agrees, differs (with the worst day and both prices), or not compared and why.

What a disagreement means: one of the two sources is wrong, or they treat a corporate event differently. It does
not say which. Nothing is corrected automatically; the lab keeps using its own source. Only ticker symbols and
dates are sent.
```

- Tables: `price_checks`
- Outside services: Tiingo (price cross-check)
- Settings: `BUDDY_TIINGO_KEY`
- Uses: gateway.py, memory.py, pricebook.py, pricestore.py, tools.py

### productid.py

```text
Product identity: which product a deal headline is about, its brand and its category.

A price is only history if the same product is filed under the same name each time. Deal headlines are not that:
"Amazon - Baseus S2 4K Solar Wireless Outdoor Security Camera $79.99" carries the store and today's price, so
every headline is a new "product" and nothing ever has a history. This module reads a headline into

    product   the brand and the model ("baseus s2"); a multi-pack is its own product ("wyze solar cam pan x2")
    brand     "baseus"
    category  "security camera"

so prices are collected per product, and can also be compared across a brand or a category when a product is new.

Travel is filed the same way, with its own words:

    flight   product = the route ("mia-lim"), place = the destination ("lim"), brand = the airline,
             category = the cabin (economy, premium economy, business, first; "not stated" when nobody said)
    hotel    product = the hotel or the place searched, place = the place, brand = the chain, category = "hotel"

A business fare is never compared with an economy one: the cabin is part of what a flight price is a price of.
Rules only, no AI model: the same headline always gives the same answer. It is a best reading, not a catalogue.
When no brand or model can be read, the cleaned headline is the product, which is still stable.
```

- Uses: shopper.py

### patterns.py

```text
Nightly pattern learning in the sandbox: Buddy hands the price book to the sandbox, which learns price patterns
(sandbox_jobs/price_patterns.py, offline, in a throwaway container) and Buddy keeps what it learned in the price book.

  export   the price book's prices, products' kinds, launches, predictions and the sale windows, as gzipped CSV
           (market data only: products, routes, hotels, prices, dates; never anything personal)
  learn    in the sandbox workspace "price-patterns", with no network: sale discounts per kind, the odds of a 10%
           drop, price against age since launch, flight fares against days before departure, hotel weekday and
           month patterns, and how Buddy's own buy-or-wait calls turned out
  keep     output/patterns.json goes into the price book's patterns table; when_to_buy reads it and prefers what
           was learned (with how many items stand behind it) over its rules of thumb

Raw prices are kept a year; the patterns are kept for good. Each month's learning is saved (the latest run of
the month wins) and merge() combines the months, weighting each by how many items or prices stood behind it; a
sale's discounts are kept per sale and year, so Black Friday 2026 is still known long after its raw prices are
gone, and Black Friday 2027 adds to it. Prices older than a year are deleted only once a successful run has
learned from them, so nothing is thrown away unlearned (without the sandbox, raw prices simply wait).

Runs once a night (02:30, before the backup) when the sandbox is set up (deploy/sandbox.sh); without it, the
patterns simply wait, and buy-or-wait keeps using its rules of thumb.
```

- Settings: `BUDDY_KEEP_RAW_DAYS`
- Uses: memory.py, pricebook.py, sandbox.py, shopper.py, tools.py, whentobuy.py

### modeltrain.py

```text
Trained models, when there is enough to learn from: each week Buddy counts its judged calls; once a domain has
enough, it trains a small model in the sandbox, tests it on calls the model never saw, and uses it only if it
earned its place. Until then, and whenever a model fails its test, the simple learned weights stay in charge.

  Domains     investing (the practice book's closed paper trades: did the trade win?), theme (the Intelligence
              Platform's evidence events: did the basket beat the market over the next 90 days?), product, flight, hotel
              (the judged buy-or-wait and book-or-wait calls: did the price then come down 8% or more?)
  Enough      200 judged rows with at least 30 of each outcome. A retrain needs 50 new rows.
  Training    sandbox_jobs/train_model.py, offline, in the sandbox: a logistic regression on the reasons and
              figures kept with each call; learns from the oldest 70%, is tested on the newest 30%.
  Promotion   only if, on the unseen calls, its odds beat always saying the average AND the calls it liked were
              right at least 5 points more often than calls overall. The test results are kept with every run.
  Use         a promoted investing model adds its odds to each signal and turns a buy it gives under 45% into a
              caution; a promoted shopping or travel model is averaged with the rule-based chance. If the latest
              run for a domain was rejected, no model is used for it (an old model that no longer passes is
              retired).

The owner is told the first time a domain has enough data, and after every training: what was tested, on how
many calls, and whether it is now in use. A model is one more opinion with a track record; Buddy never trades.
```

- Tables: `models`
- Uses: memory.py, pricebook.py, sandbox.py, simbook.py, stratlab.py, themes.py, tools.py, whentobuy.py

## How it connects

- Relies on: Buddy Connect; Buddy Intelligence Platform; Claude: the brain; Fixing and explaining itself; Investing: signals, research and practice; Memory; Shopping and deals
- Relied on by: Buddy Intelligence Platform; Claude: the brain; Home and health; Investing: signals, research and practice; Learning: the English and investing tutors; Shopping and deals; Speaking up on its own: briefing, reminders and alerts; The server and the app; Travel and getting around

