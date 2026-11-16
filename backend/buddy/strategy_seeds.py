"""Strategies read from research papers the owner supplied, shipped as proposed rules.

On 16 November 2026 the owner uploaded eleven SSRN papers. Each was read for the trading rule it tests, and the
rule was written in the lab's fixed form (stratrules.py) using only figures Buddy measures. They load once as
PROPOSED: nothing trades until the owner approves each one in the Trading tab, and a removed one stays removed.

What these are and are not:
  - The papers themselves are not stored here, only a rule in Buddy's own words and a link to each paper.
  - Each rule holds part of its paper, never all of it: the papers test long-and-short portfolios, re-formed
    monthly, often outside the US, and several report that the strategy did NOT work in their market. What was
    left out is listed on every rule, and the paper's own finding is in its summary.
  - A paper's result is not Buddy's result. The replay over past prices and the paper trades are the test.
Two of the eleven gave no rule of their own and are listed in NOT_RULES with the reason. Papers that test
the same idea share one rule, so eleven papers give seven rules.
"""

SSRN = "https://ssrn.com/abstract="
SEEDS = [
    {"id": "u_faber_10_month_average", "source": "Faber: A Quantitative Approach to Tactical Asset Allocation",
     "source_url": SSRN + "962461",
     "rule": {"name": "Faber 10-month average", "universe": ["SPY", "QQQ", "IWM", "DIA", "GLD", "DBC"],
              "summary": "Holds a fund while its price is above its long average and sells when it falls below. The "
                         "paper reports stock-like returns with smaller falls across five asset classes.",
              "buy": [{"field": "above_200", "op": ">", "value": 0}], "rank": None,
              "sell": [{"field": "above_200", "op": "<", "value": 0}], "hold_days": 120,
              "needs": ["foreign stock, bond and real estate funds, which are not in the lab's list",
                        "checking once at each month's end: Buddy checks every day, on the 200-day average",
                        "interest earned on cash while out of the market"]}},
    {"id": "u_52_week_high", "source": "Raju (India); Malin and Bornholt (emerging-market indices); Alsubaie and Najand (Saudi market)",
     "source_url": SSRN + "4587697",
     "rule": {"name": "Near the 52-week high", "universe": "stocks",
              "summary": "Buys the stocks closest to their one-year high (George and Hwang's rule). The three papers "
                         "disagree: it worked in India, was unprofitable on emerging-market indices, and reversed in the "
                         "Saudi market.",
              "buy": [{"field": "below_high", "op": ">=", "value": -0.05}],
              "rank": {"field": "below_high", "order": "high", "top": 10}, "sell": [], "hold_days": 120,
              "needs": ["selling short the stocks furthest from their high",
                        "monthly re-forming with overlapping six-month holdings",
                        "the markets the papers tested: Buddy tests large US stocks"]}},
    {"id": "u_low_beta_low_lottery", "source": "Bali, Brown, Murray and Tang: Betting against Beta or Demand for Lottery",
     "source_url": SSRN + "2481344",
     "rule": {"name": "Low beta, not a lottery ticket", "universe": "stocks",
              "summary": "Buys calm stocks (low beta) that have not had big one-day jumps lately. The paper finds the "
                         "low-beta effect comes from investors overpaying for stocks with lottery-like jumps.",
              "buy": [{"field": "beta", "op": "<=", "value": 0.8}],
              "rank": {"field": "lottery", "order": "low", "top": 10}, "sell": [], "hold_days": 28,
              "needs": ["selling short high-beta stocks", "borrowing so both sides carry the same market risk"]}},
    {"id": "u_earnings_drift", "source": "Kovacs; Lee and Rui; Chordia and Shivakumar (post-earnings-announcement drift)",
     "source_url": SSRN + "972332",
     "rule": {"name": "Earnings surprise drift", "universe": "stocks",
              "summary": "Buys companies whose latest profit beat what analysts expected, on the finding that prices "
                         "keep drifting the surprise's way for weeks afterwards.",
              "buy": [{"field": "earnings_surprise", "op": ">=", "value": 0.05}],
              "rank": {"field": "earnings_surprise", "order": "high", "top": 10}, "sell": [], "hold_days": 60,
              "needs": ["the date of the earnings report: Buddy knows the surprise, not how many days ago it came",
                        "the surprise measured against the company's own history (standardised)",
                        "same-industry companies confirming the surprise (Kovacs)",
                        "inflation, which the drift varies with (Chordia and Shivakumar)",
                        "selling short companies that missed"]}},
    {"id": "u_market_state_momentum", "source": "Huang, Jiang, Tu and Zhou: Forecasting Stock Returns in Good and Bad Times",
     "source_url": SSRN + "2188989",
     "rule": {"name": "Past-year return in a weak market", "universe": "indexes",
              "summary": "Buys an index fund that is below its 200-day average but still up over the past year. The "
                         "paper finds the past-year return predicts higher returns in bad times and lower in good times.",
              "buy": [{"field": "above_200", "op": "<", "value": 0}, {"field": "ret_252", "op": ">", "value": 0}],
              "rank": None, "sell": [], "hold_days": 28,
              "needs": ["the good-times half: in a rising market the paper finds a low past-year return is the better sign",
                        "the paper forecasts the size of next month's return; this is only a buy condition"]}},
    {"id": "u_profitable_and_growing", "source": "Hou, Mo, Xue and Zhang: Security Analysis: An Investment Perspective",
     "source_url": SSRN + "3415546",
     "rule": {"name": "Profitable and growing", "universe": "stocks",
              "summary": "Buys the most profitable companies whose profits are growing. The paper explains expected "
                         "returns by profitability, how fast a company invests, and its expected growth.",
              "buy": [{"field": "roe", "op": ">=", "value": 0.15}, {"field": "eps_growth", "op": ">", "value": 0}],
              "rank": {"field": "roe", "order": "high", "top": 10}, "sell": [], "hold_days": 120,
              "needs": ["how fast the company is growing its assets (investment), which the paper says to avoid when high",
                        "expected growth as the paper measures it", "re-ranking each quarter on new reports"]}},
    {"id": "u_volume_momentum", "source": "Alsubaie and Najand: Trading Volume, Price Momentum and the 52-Week High (Saudi market)",
     "source_url": SSRN + "1087391",
     "rule": {"name": "Momentum with rising volume", "universe": "stocks",
              "summary": "Buys past winners whose trading volume has been rising. The paper finds momentum profits in the "
                         "Saudi market come from high-volume winners.",
              "buy": [{"field": "ret_126", "op": ">", "value": 0}, {"field": "volume_trend", "op": ">", "value": 1}],
              "rank": {"field": "ret_126", "order": "high", "top": 10}, "sell": [], "hold_days": 120,
              "needs": ["volume compared across all stocks: Buddy compares each stock's volume with its own past year",
                        "selling short the losers", "the Saudi market: Buddy tests large US stocks"]}},
    # --- widely used rules with no single source, added so they can be tested on the same terms as the rest ---
    {"id": "u_golden_cross", "source": "A widely used rule (no single paper)", "source_url": "", "licence": "common knowledge",
     "rule": {"name": "50-day above 200-day", "universe": "all",
              "summary": "Holds while the 50-day average is above the 200-day average (the 'golden cross' condition) "
                         "and sells when it drops below.",
              "buy": [{"field": "avg50_above_200", "op": ">=", "value": 1}, {"field": "above_50", "op": ">", "value": 0}],
              "rank": {"field": "ret_63", "order": "high", "top": 10},
              "sell": [{"field": "avg50_above_200", "op": "<", "value": 1}], "hold_days": 120,
              "needs": ["the day of the cross itself: Buddy buys whenever the condition holds, not only on the day it starts"]}},
    {"id": "u_macd_trend", "source": "A widely used rule (no single paper)", "source_url": "", "licence": "common knowledge",
     "rule": {"name": "MACD trend", "universe": "all",
              "summary": "Buys when MACD is above its signal line in a long-term rise, and sells when it drops below.",
              "buy": [{"field": "macd_hist", "op": ">", "value": 0}, {"field": "above_200", "op": ">", "value": 0}],
              "rank": {"field": "macd_hist", "order": "high", "top": 10},
              "sell": [{"field": "macd_hist", "op": "<", "value": 0}], "hold_days": 60,
              "needs": ["the day MACD crosses its signal line: Buddy buys whenever it is above"]}},
    {"id": "u_dual_momentum", "source": "A widely used rule (no single paper)", "source_url": "", "licence": "common knowledge",
     "rule": {"name": "Dual momentum", "universe": ["SPY", "QQQ", "IWM", "DIA", "GLD", "DBC"],
              "summary": "Holds the one fund with the best past-year return, but only while that return is positive "
                         "(relative and absolute momentum together).",
              "buy": [{"field": "ret_252", "op": ">", "value": 0}], "rank": {"field": "ret_252", "order": "high", "top": 1},
              "sell": [{"field": "ret_252", "op": "<", "value": 0}], "hold_days": 28,
              "needs": ["foreign stock and bond funds, which are not in the lab's list",
                        "comparing the return with what cash paid, instead of with zero"]}},
    {"id": "u_low_volatility", "source": "A widely used rule (no single paper)", "source_url": "", "licence": "common knowledge",
     "rule": {"name": "Low volatility", "universe": "stocks",
              "summary": "Buys the ten calmest stocks that are in a long-term rise.",
              "buy": [{"field": "above_200", "op": ">", "value": 0}], "rank": {"field": "vol_63", "order": "low", "top": 10},
              "sell": [], "hold_days": 60, "needs": ["re-ranking the whole list each month"]}},
    {"id": "u_quality_value", "source": "A widely used rule (no single paper)", "source_url": "", "licence": "common knowledge",
     "rule": {"name": "Quality at a fair price", "universe": "stocks",
              "summary": "Buys profitable companies with modest debt that are not expensive against their profits.",
              "buy": [{"field": "roe", "op": ">=", "value": 0.15}, {"field": "debt_to_equity", "op": "<=", "value": 1},
                      {"field": "pe", "op": ">", "value": 0}, {"field": "pe", "op": "<=", "value": 20}],
              "rank": {"field": "pe", "order": "low", "top": 10}, "sell": [], "hold_days": 120,
              "needs": ["a fuller measure of value and quality: cash flow and several years of profits"]}},
]
NOT_RULES = [
    {"source": "Marmi, Pacati, Reno and Risso: A quantitative approach to Faber's tactical asset allocation",
     "source_url": SSRN + "1476225",
     "why": "A critique, not a strategy: it argues Faber's result should be tested with resampling before it is believed. "
            "That is what the replay and the split by period are for."},
    {"source": "Malin and Bornholt: Predictability of Future Index Returns Based on the 52-Week High Strategy",
     "source_url": SSRN + "1714077", "why": "Tests the same 52-week-high rule on indices; folded into that rule."},
]
