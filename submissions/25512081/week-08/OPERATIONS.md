# Current operating instructions — 2026-11-08

This is the current scoring and measurement policy. It supersedes scoring,
deadline-clearance and model descriptions in older overseer history. Keep the
history: do not edit existing logs, erase failed trials, amend or squash commits.

## Objective and models

- Optimize the Class Players score (shop + buyer), computed among students.
- Shop: weighted percentiles of Book 0.5, revenue 0.3, stars_rank 0.2.
  Book = cash - starting capital - bankruptcy injections + stock at cost.
  Unsold goods are not automatically lost. Expiry, storage, refunds and selling
  below cost can reduce Book; the deadline alone is not a reason to dump goods.
- Stars take one mean per reviewing GitHub account. Do not confuse transaction
  average `stars`, lifetime `stars_rank`, and our bounded observation average.
- Buyer: Saved percentile 0.5 + forum rating itself 0.3 + deals percentile 0.1
  + posts percentile 0.1. Saved is normalized per-deal surplus / list price,
  summed and divided by deals + 100, not raw surplus in credits.
- Candidate shortlisting uses Haiku (`harness.CHEAP`). Keep exception negotiation
  Sonnet, manager Opus and code overseer Opus. Do not revert the shortlist model
  due solely to a shared subscription-limit error.

## Evidence collection

`shop_metrics.py` runs without Claude every five minutes, started by restock.
Read `.market/shop-metrics.json`; if stale, run:

```bash
python shop_metrics.py --pages 24
```

It has a lock and a resumable ledger cursor. Failed pages do not advance past a
gap. `logs/shop-metrics-events.jsonl` and `shop-metrics-snapshots.jsonl` append
facts. The first bootstrap was bounded to 60,000 entries (274 linked paid
reviews); this is not a reconstruction of lifetime reviewer means. The collector
states its earliest observation and backlog. `shadow_alternatives` uses mixed
grades and estimated costs, excludes warranty/storage, and never buys anything.
The MCP `shop_quality` tool exposes the bounded evidence to the manager.

Baseline on Nov 8: Class total ~1.518 versus leader ~1.648; our shop stars_rank
4.27, 39/44 among student shops, versus our Book rank 10 and revenue rank 12.
In the 274-review window, food A averaged 4.80, B 4.70, C 4.14; four reviews
explicitly reported insufficient shelf life. Price bins did not show a stable
monotonic relationship to stars. These are associations, not controlled effects.
There were also 1-star reviews that mistook valid category/SKU receipt differences
for forgery. Category-only orders now retain the category in the signed receipt;
the unit and ACP checkout still bind the actual SKU.

## Rollout order and gates

1. Observe food requirement and receipt-identity fixes before changing prices.
   Seller rejects food that cannot meet a stated duration plus one minute for
   checkout; rechecks at signing. With no stated duration it retains the existing
   freshness heuristic and honestly states hours left. Hidden buyer requirements
   cannot be guaranteed. Grade C is not banned merely because it is grade C.
2. Each two-hour overseer report compares the latest complete supply windows
   with the previous matched windows: paid seller deals, Book change, revenue,
   reviewer-group means, shelf-life complaints, refunds, refused checkouts and
   buyer Saved. Distinguish changing trade mix from within-product improvement.
3. Price experiments require comparable SKU/grade/warranty observations, at
   least 15 reviews in each compared price bucket and three reviewing accounts.
   Check the same accounts where possible. No global discount based on one
   review or one aggregate average. Try at most one category, a 0.02 change in
   ask/list share, and keep the normal cost floor. The existing write tool's
   evidence checks still apply. Do not personalize prices by reviewer identity.
4. For stock, examine `shadow_alternatives`, real recent grade-matched prices,
   surge and our sell-through. Test at most one alternative SKU, 1-2 units,
   within existing cash and stock caps. Measure for at least two supply windows
   (40 minutes); expand only after observed profitable sell-through. If sparse,
   wait for more observations instead of assuming all inventory will sell.
5. A below-cost liquidation reduces Book immediately. Do not do it to make cash
   look better. Compare likely recovery, storage, spoilage and refund costs first.
   Existing expensive inventory must not justify unlimited replacement purchases.

If a trial hurts matched Book growth and does not improve observed account-level
stars, revert with a new commit. A short-window correlation is not proof of a
model or pricing advantage. Report sample size and unresolved uncertainty.

## Verification and deployment

- The two-hour code overseer is wrapped by `usage_guard.py`. Read native
  `claude -p /usage` before launch and every 60 seconds during execution. At
  session >=80% or all-model week >=70%, pause; unreadable usage also pauses.
  Thresholds are in `usage_limits.json`, matching ADMIL's existing session/week
  settings. These are shared-account percentages, not a market-only budget.
  Resume on a later scheduled run only when a fresh check passes. Check
  `logs/overseer-quota.jsonl` and `paused` in overseer/runs.jsonl. Interrupted
  output is saved and its cost is unknown, not zero; inspect uncommitted changes
  before continuing. Do not remove the quota wrapper as an optimization.
  This guard controls the code overseer only; normal buyers, sellers and hourly
  stock planning continue and can still increase shared-account usage.
- Use production Python `~/market-agent/.venv/bin/python`, not system Python 3.7.
- Run `test_shop_operations.py` on isolated copies. Set `MARKET_KIT_PATH` to
  the repository's scripts/market_kit.py to check signed category receipt → SKU
  ACP checkout without any real market writes. No Claude or private keys needed.
- Never test against live memory or send fabricated orders into the real market.
- Per-order scripts take effect on the next process. Seller changes require a
  controlled kit restart after a supply window closes; identify exact PIDs,
  allow current work to finish, then let keepalive restart. Verify card, ACP,
  new processes and real settlement logs. Never kill ADMIL or unrelated jobs.
- Preserve logs, secrets and failed history. Commit each unit; never push as
  part of an unattended overseer run. Record actual validation, not claimed gains.
