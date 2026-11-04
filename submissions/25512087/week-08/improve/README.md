# Self-improvement loop (week 08, Round 3)

Every 3 hours, while Round 3 runs (11/3 21:02 to 11/17 23:59 KST):

1. **Measure**: `python3 improve/measure.py` writes `metrics/<KST time>.json`. It covers the window since the last run and the total since Round 3 opened, read from `logs/market-kit.log` and `.market/`. Logs are only read.
2. **Report**: `reports/R3-<n>-<time>.md` covers the numbers, what changed in the market, what worked, what did not, and the decision.
3. **Analyze**: market data from the public ledger, `/api/stats` and the forum, alongside our own records.
4. **Improve**: `python3 improve/tune.py` moves numeric knobs one step at a time, always inside `tuning.BOUNDS`. It writes `policy.json` and appends to `changes.jsonl` with the evidence. It rolls a change back when the objective (shop margin + buyer surplus per hour) drops by more than 40%.
5. **Deploy**: the seller, restock and market_buyer re-read `policy.json` on every negotiation or run, so no restart is needed.
   - Code changes are only proposed in the report. A human approves them before they ship.

Human-in-the-loop: the knobs and their bounds were set by a person, and code changes need a person's approval. This loop is the "fast loop" in REPORT.md (0); the person's review is the "slow loop".
