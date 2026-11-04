# Week 8: Round 3 first-run setup

## Student's selected rules

- Sell both food and goods. Buy one unit per selected SKU.
- Initial cumulative stock allowance: at most 2,000,000 credits.
- Open at 5% above actual unit cost, rounding up to whole credits.
- Hold the full 5% price for two counteroffers. After that, accept an offer
  at or above 3% above cost; otherwise counter at the 3% minimum.
  A buyer offering the current minimum or more can close immediately.
- For food, once half the time between `supplied_at` and `expires_at` has elapsed,
  allow the 3% minimum immediately, without two counteroffers. Opening ask stays
  at 5%. Expired food remains unsellable. Goods keep the two-counteroffer rule.
- Buyer requires a market certificate. Food must last at least the order's
  `use_within_hours`. Grades A/B/C are accepted; no goods warranty is required.
- When an asking price becomes affordable, the buyer requests one discount.
  The model selects the amount (no fixed discount percentage). If the seller
  continues with an affordable counteroffer, the buyer accepts that price.
  Above-budget asks still follow the original budget-limited negotiation rule.
- Seller makes no grade or warranty promises; the kit attaches the certificate.
- Dispatch matching available units by FIFO (earliest `supplied_at` first), for
  both food and goods. Expired and held units are excluded before FIFO selection.

Configuration is in `round3_rules.json`. It is enabled for the first run.
The old 15% fixed-reserve adaptation applies only to the retained Round 2 path.

## Files and responsibilities

- `seller_strategy.py`: inventory, pricing, model decisions, stock selection.
- `seller.py`: A2A server, unit holds, signed receipts, append-only event history.
- `buyer.py`: A2A negotiation and budget/task/agreed-price receipt checks.
- `market_buyer.py`: order-aware shop/SKU choice, memory and acceptance policy.
- `harness.py`: authenticated CLI calls with raw attempt logs.
- `agent.json`: kit commands, including the restock hook and order-file argument.
- `test_agent.py`: offline checks, including retained Week 7 regression tests.
- `join_market.ps1`: preflight and foreground launch.
- `market_runner.py`: loads repository kit v4 unchanged and tracks supply outcomes.

Stock selection uses a bounded catalog sample spread across categories and both
product types. Confirmed own shop settlements (matched by unit to the kit log)
and the buyer's paid purchases prioritize observed products and categories.
These observations are not a claim about global popularity. The model chooses
at most ten SKUs per hook invocation. One unit per SKU may be in stock; a sold-out
SKU can be purchased again while the original cumulative budget allows it.

Code reserves each SKU's worst-case supply cost (75% of list price) before the
request. The runner records the exact reservation ID and API outcome. Confirmed
purchases consume their actual cost, confirmed refusals and unattempted requests
consume zero, and ambiguous/in-flight requests retain their full reservation.
A closed supply window defers the remaining batch and pauses new stock selection
until the stated opening time. There is no automatic budget reset or reinvestment.
Preserve both `.market/restock-reservations.jsonl` and `restock-outcomes.jsonl`.
Outcome copies are also appended under `logs/` for grading evidence.

On November 4, old logs uniquely confirmed 95 purchases costing 1,050,537 credits
and 30 refusals. Three unresolved requests remain reserved at 44,580 credits.
Reconciliation restored 904,883 credits of allowance within the unchanged
2,000,000 cap. Original reservation rows and raw logs were preserved; the repair
snapshot and report are under `logs/setup/stock-repair-20261104T010846_513969Z/`.
The code includes idempotent legacy reconciliation; no uncertain result is freed.

The seller holds units during negotiation and checkout handoff, with a five-minute
timeout. A signed receipt is an agreement, not proof of settlement. Buyer memory
reads `.market/payments.jsonl` on later orders to distinguish paid/refused trades.
Neither agreed sales nor synthetic checks count as real Round 3 settlements.

## Start from the repository root (PowerShell)

Stop the old Week 7 kit with Ctrl+C before launching the same shop identity.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File submissions/26512073/week-08/join_market.ps1 -Check
powershell -NoProfile -ExecutionPolicy Bypass -File submissions/26512073/week-08/join_market.ps1
```

The launcher asks for the claim code privately if it is absent. It sets UTF-8,
uses the Week 8 environment and `market_runner.py`, which loads the unchanged
repository `scripts/market_kit.py` (v4), and
finds the existing ignored cloudflared binary in Week 7. Keep this terminal and
PC running. Ctrl+C stops the kit. There is no scheduled start or automatic join.
The v3 copy in `course_support/` remains historical material; do not launch it.

Check `logs/market-kit.log` for kit version 4, both registrations in round 3,
`[supply]` entries, and `[acp] paid` / `[acp] settled` entries. Before the market
switches rounds, the kit uses Round 2; those results do not satisfy the assignment.

## Verification and limits

Offline checks and synthetic integration logs are under `logs/setup/`. Real
model probes use synthetic offers/catalogs and are labeled; raw CLI attempts
are under `development_logs/`. The first real market settlement is still to be
verified. Official Week 8 README/checker files were absent at setup; these checks
are not a substitute for later course grading requirements.

```powershell
Push-Location submissions/26512073/week-08
& ./.venv/Scripts/python.exe -m unittest test_agent -v
Pop-Location
```

Identity keys are local and Git-ignored. The previously exposed buyer key was
copied to retain identity, not rotated; that identity issue remains unresolved.
No key, API token, or claim code belongs in commits. Existing Week 8 pricing
migration notes and historical logs are preserved. The final report, student's
LaTeX architecture figure, forum strategy, and market results remain future work.
