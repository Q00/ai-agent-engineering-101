# First-quote concession pilot (default OFF)

Only the first public opening ask changes: A retains the original quote; B asks
max(reserve, round(original_open * .97)). The deal's original open stays intact as
the concession-schedule anchor. Inventory selection, reserve, warranty, reader,
buyer, budget, bankruptcy, metadata and forum policies are unchanged.
Existing counter guards still cap subsequent counters at the current ask.

## Configuration
In MARKET_DIR, create seller_concession_trial.json with this schema:
```json
{"enabled":true,"id":"unique-price-pilot","start":1,"end":1801,"seed":42,"stop":false}
```
Replace timestamps with epoch seconds, 0 < end-start <= 1800. All fields except
stop are required while enabled. Missing, disabled, invalid or stopped config
fails closed to A. No config is shipped or enabled by this change.
Keep the reader pilot disabled throughout: this is not a factorial trial.

Assignments persist in seller_concession_trial.sqlite3; quote/outcome events in
seller_concession_trial.jsonl. Neither touches the reader pilot files.
A full task UUID is sticky across process restarts. Assignment precedes the public
quote and stores original opening/cost/reserve even if the buyer never replies.
Strata: kind × original price <300000/≥300000 × relative 5-minute bin.
Permuted blocks of two, total <=200, B<=100 (can stop at 199 if B hits cap).
After end, existing assignments and quoted prices remain; new tasks are A.
Do not change id/start/seed during a run or delete the assignment DB.

## Readout
Use seller_concession_trial.Trial(root).readout(id, now=epoch) locally.
Each arm reports assigned, mature (600 seconds), deals and gross_margin.
Only terminal deal events within 600 seconds of enrollment count.
No-reply and canceled tasks remain in ITT denominator. Gross margin is price minus
unit cost, NOT final Players profit: warranty refunds, holding costs and later
outcomes require the existing ledger/Players reconciliation by the operator.

## Stop / rollback
Set stop:true or enabled:false. Unissued/new quotes use control. On the first
reply an untouched initial ask restores to the original when stopped/invalid;
later negotiated counters are never raised. Already accepted transactions cannot
be undone. Preserve config/DB/trace until the 600-second follow-up matures.
Code rollback: revert the implementation commit after stopping enrollment.
No push, merge, service restart or live configuration write is part of this change.
