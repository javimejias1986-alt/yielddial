# YieldDial: what your dollar earns right now

Static site (plain HTML/CSS/JS, no build step, no backend) comparing cash yields: US T-bills, money-market funds, Aave v3 and Compound v3 USDC, and exchange USDC rewards.

## Preview
```bash
cd yielddial
python3 -m http.server 8000     # then open http://localhost:8000
```
(Opening index.html via file:// won't work: the page fetches data/rates.json.)

## Refresh data
```bash
python3 scripts/fetch_rates.py                 # all sources
python3 scripts/fetch_rates.py --only treasury # or defi / manual
```
Python 3.9+, stdlib only. Writes `data/rates.json`. Exits non-zero on bad data and never invents numbers: a failed row keeps its old value and old timestamp, so the page's "stale" banner (older than 36 h) fires.

## Sources
| Rows | Source | Mode |
|---|---|---|
| Treasuries 1M/3M/6M/1Y + curve | Treasury daily par yield curve CSV | live |
| Aave v3 USDC (Ethereum, Base, Arbitrum, Optimism) | https://api.v3.aave.com/graphql (official) | live |
| Compound v3 USDC (Ethereum, Base, Arbitrum) | Comet contracts via public RPC (`getUtilization` -> `getSupplyRate`) | live |
| Money-market funds, exchange rewards | `data/manual.json` | manual, update weekly |

DefiLlama is deliberately not used (commercial-use terms).

## Manual updates
Edit `data/manual.json` (`yield_pct`, `as_of`, `checked_on`, `source_url`), then run `python3 scripts/fetch_rates.py --only manual`.

## Placeholders to fill before launch
- `affiliate_url` per row in `data/manual.json` (currently `#`; links are inert until set) and the affiliate disclosure text in `index.html`.
- Newsletter form in `index.html` (currently inert; wire to a provider).

## Scheduling
`.github/workflows/update.yml`: Treasury weekdays 22:15 UTC (after 17:00 US Eastern), DeFi every 2 h. Not pushed anywhere yet.
