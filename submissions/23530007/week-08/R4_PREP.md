# Round 4 preparation (written 2026-11-11, from market/ROUND4.md)

Round 4 = Round 3 + real transfers on Maroo testnet + fees. `/api/chain` and the R4 kit were not live on 11-11, so
nothing below is wired yet; build and test it on R4 day 1 against the real `/api/chain`.

## What changes for us

| Rule | Effect on our agents |
|---|---|
| Each side pays 1% of the price + 210 credits | buyer's cost in score = `P + m + 210` (m = 1% of P); shop receives `P - m - 210` |
| Only the buyer's wallet sends; 5,000 tOKRW per faucet claim (captcha) until it holds 10,000 | a human must claim; an empty wallet means the buyer silently stops paying |
| Fees are not returned when goods break | a refunded break now scores slightly below 0 for the buyer |

## Code changes to make on R4 day 1

1. Fee-aware buyer value: `v = 1 - (P + 0.01P + 210) / list` in `buyer_score.score`, `buyer_value._saved`, `market_buyer`
   caps and the goods ceilings `x_bar_g` (the 210-credit part costs 2% on a 10k snack, 0.02% on a 1M sofa: cheap food
   loses most). Recompute m* after the change.
2. Fee-aware shop floor: `floor >= cost x floor_mult + 0.01P + 210`; items with list under ~20k may stop paying.
3. `RULES_THAT_SCORE.md`: add the fee table so the strategists read it.

## Tools to add (market_tools.py, read-only) and guards

- `chain_status()`: `/api/chain` (network, fees, market address) + our buyer wallet balance and pending payments.
- `fees_paid(hours)`: fees per deal and their share of saved, by kind.
- Guard G13 (guards.py): buyer wallet under 3,000 tOKRW -> phone alert "faucet claim needed" (only the user can pass the
  captcha); under 500 -> the buyer strategist is woken and told deals will fail.
- `ops/status.py`: wallet balance line.

## Budget

The API credit expires 2026-11-21 00:00 UTC (no refill, no card). After that every agent call runs on the CLI login
(shared Claude Code limit): plan R4 with the buyer strategist every 2 h and the tools on, or decide with the user.
