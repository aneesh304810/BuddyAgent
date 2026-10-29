# Money: where Buddy's numbers come from

| Source | What it gives | Cost | Where it works |
|---|---|---|---|
| **Bank feed (SimpleFIN Bridge)** — recommended | Every transaction and every balance, straight from the banks and card issuers, read-only | About $15 a year (or $1.50 a month) to SimpleFIN | Most US banks and cards (Chase, Amex, Citi, Capital One, Bank of America, credit unions...) |
| **Bank and card emails** | Purchases from alert emails, plus **bills and due dates** from statement emails | Nothing extra: read during email sorting | Anywhere, including banks in Peru, Brazil and India that email alerts |
| **By voice** | Cash and anything else | Nothing | Anywhere |

With the bank feed on, Buddy takes transactions only from the feed (exact, nothing missed)
and uses emails just for bills, so nothing is counted twice. Card payments and transfers
between your own accounts are skipped automatically.

## Connect the bank feed (about 10 minutes, all on your phone)

1. In Chrome, open **bridge.simplefin.org**, create an account and subscribe.
2. Tap **Connect a bank** (or **New connection**) and log in to each bank or card. This
   happens on SimpleFIN's site through MX; Buddy never sees your bank passwords.
3. Tap **New app connection** (or **Get setup token**), name it `Buddy`, and copy the
   **setup token** (a long string of letters).
4. On the server (`ssh root@buddy.<your-tailnet>.ts.net`):
   ```
   sudo bash /opt/buddy/deploy/bank.sh
   ```
   Paste the token when asked (it's hidden). It works once: it's exchanged for a read-only
   access link that stays on your server.
5. Ask Buddy: *"What are my account balances?"*, *"How much did I spend this month?"*

Buddy fetches the last 30 days straight away, then every 6 hours (SimpleFIN allows up to 24
updates a day). Each new shop name is sorted into a category once by the cheap model and
remembered, a fraction of a cent. Add the subscription to your cost report:
`sudo bash /opt/buddy/deploy/set.sh BUDDY_FIXED_COSTS` → `SimpleFIN=1.25`.

To disconnect: delete the app connection on bridge.simplefin.org (that revokes the link),
or `sudo bash /opt/buddy/deploy/set.sh BUDDY_SIMPLEFIN_URL` and press Enter on an empty value.

## Other countries and other services

| Service | Why it isn't the default |
|---|---|
| **Plaid**, **Teller**, **Yodlee**, **MX** directly | Built for companies: developer accounts, approval and compliance reviews before real bank data. SimpleFIN resells MX to individuals. |
| **Belvo**, **Pluggy** (Brazil, Mexico, Colombia), **Prometeo** (Peru, LatAm) | Business-only contracts today. Bank emails and phone notifications cover these banks meanwhile. |
| **India's Account Aggregator** network (Setu, Finvu, OneMoney) | Licensed for regulated businesses; personal API access isn't offered. Use bank alert emails or SMS (via phone notifications). |
| **Monarch**, **Copilot**, **YNAB** | Apps with their own bank links; no official API for your own data. |

If one of these opens personal access, Buddy's agent builder can connect it.
