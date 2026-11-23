# Self-improvement loop (week 08, Round 3)

Every 3 hours, while Round 3 runs (11/3 21:02 to 11/17 23:59 KST):

1. **Measure**: `python3 improve/measure.py` writes `metrics/<KST time>.json`. It covers the window since the last run and the total since Round 3 opened, read from `logs/market-kit.log` and `.market/`. Logs are only read.
2. **Report**: `reports/R3-<n>-<time>.md` covers the numbers, what changed in the market, what worked, what did not, and the decision.
3. **Analyze**: market data from the public ledger, `/api/stats` and the forum, alongside our own records.
4. **Improve**: `python3 improve/tune.py` moves numeric knobs one step at a time, always inside `tuning.BOUNDS`. It writes `policy.json` and appends to `changes.jsonl` with the evidence. It rolls a change back when the objective (shop margin + buyer surplus per hour) drops by more than 40%.
5. **Deploy**: the seller, restock and market_buyer re-read `policy.json` on every negotiation or run, so no restart is needed.
   - Code changes are only proposed in the report. A human approves them before they ship.

Human-in-the-loop: the knobs and their bounds were set by a person, and code changes need a person's approval. This loop is the "fast loop" in REPORT.md (0); the person's review is the "slow loop".

## 11/17: code behind flags (structure change ④)

Code proposals waited a week for approval while the Mac was offline, so the loop changed only numbers. From 11/17:

1. A code change ships **off**: its behaviour sits behind a flag in `improve/policy.json`, declared with bounds in
   `tuning.BOUNDS` (0 = off). Examples: `seller.endgame_ask_mult`, `buyer.shelf_verify`.
2. It ships only with its own unit tests (`test_round3_1117.py`) and with the full suite passing.
3. Turning a flag on is a knob change: it is logged in `changes.jsonl` with its evidence and the number that should move.
4. `tune.py`'s rollback treats flags like any knob: if the objective falls by more than 40 % in the next window, the
   flag goes back off by itself. The human reviews afterwards instead of gating before.
5. Uptime is part of the loop: `ops/watchdog.sh` (launchd, every 2 min) restarts the kits when the log is idle 10 min.
