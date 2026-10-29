# FinOps: what Buddy costs to run

Ask Buddy *"What do you cost to run?"*, *"What's my Buddy bill this month?"* or
*"Where's the money going?"*. You get one all-in answer: the total so far this month, the
end-of-month forecast, the biggest line first, and whether you're within budget.

| Line | Where the number comes from | Set-up |
|---|---|---|
| **Claude** | Buddy meters every call it makes (tokens and web searches, at list prices), split by purpose: chat, Listen-in, "let me think", expert, research, plus web searches | None: always on |
| Claude (exact bill) | Anthropic's cost report | Optional, **organizations only**: an Admin API key (`sk-ant-admin...`). Individual accounts can't get one; Buddy's own meter is then used and is close to the bill |
| **DigitalOcean** | Your live month-to-date bill (droplet, backups, anything else on the account) | A DigitalOcean token; without it, the plan price, spread over the month |
| **Zapier** | Your plan price, plus Buddy's count of Zapier calls (about 2 tasks each) against your monthly task allowance | Plan price and allowance (Zapier has no billing API) |
| **Anything else** | Fixed monthly amounts you list: Tailscale, Backblaze backups, a domain, Twilio, ElevenLabs, xAI... | One line in the settings file |

Forecasts assume the rest of the month looks like the days so far. Answers from
DigitalOcean and Anthropic are cached for 30 minutes.

## Set it up from your phone (about 5 minutes)

1. **DigitalOcean token (recommended).** In Chrome, open **cloud.digitalocean.com → API →
   Tokens → Generate New Token**. Name it "Buddy costs", pick **Custom scopes** and tick
   only **billing: read** (if your account doesn't offer custom scopes, choose **Read**
   only). Copy the token; it's shown once.
2. **Anthropic Admin key (only for an organization account).** **console.anthropic.com →
   Settings → Admin keys → Create**. Skip this on an individual account.
3. In Termux, `ssh root@buddy`, then `nano /etc/buddy/buddy.env` and add what applies:
   ```
   BUDDY_DO_TOKEN=dop_v1_...
   # BUDDY_ANTHROPIC_ADMIN_KEY=sk-ant-admin...      # organizations only
   BUDDY_COST_SERVER_USD=12                       # droplet price, used without a token
   BUDDY_ZAPIER_PLAN_USD=0                        # 0 on the free plan
   BUDDY_ZAPIER_TASKS_PER_MONTH=100
   BUDDY_FIXED_COSTS=Tailscale=0, Backblaze=0.50, Domain=1.25
   BUDDY_ALLIN_BUDGET_USD=60                      # Buddy warns when the forecast is over
   ```
4. `sudo systemctl restart buddy`, then ask *"What do you cost to run?"*

The tokens only read billing figures: they can't create servers or spend money. They stay in
the server's settings file, like your other keys. To revoke one, delete it on the same page.

## The numbers as data

```
curl -H "Authorization: Bearer TOKEN" "https://<server>/admin/costs?fresh=true"
```
returns `month_to_date_usd`, `forecast_usd`, `all_in_budget_usd`, `over_budget`, one entry per
line (`provider`, `month_to_date`, `forecast`, `source`: live / buddy meter / plan price / fixed,
`note`) and `claude_by_purpose`.

Two money controls work together:
- **Thrift** (`BUDDY_MONTHLY_BUDGET_USD`, daily allowances) limits Claude spending and asks
  before going over. `spending_status` covers today.
- **FinOps** (`BUDDY_ALLIN_BUDGET_USD`) only reports. It covers everything, all month.

## Adding a provider with a billing API

Add a method to `FinOps` in `backend/buddy/finops.py` that returns a `CostLine`. Read the
token from `self.setting(...)`, call the API through `self.fetch`, and fall back to a plan
price when the call fails. Then call it from `report()`. Add a test with a fake API, like
`backend/tests/test_finops.py`.
