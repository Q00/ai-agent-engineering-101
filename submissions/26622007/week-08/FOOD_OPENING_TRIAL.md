# Food opening price trial (default OFF)

A keeps the adopted 3% opening. B proposes 4% from the unchanged original opening,
clamped at the existing baseline first-round floor and reserve. Goods, original
negotiation anchor, expiry, warranties and all final price guards remain unchanged.

Population: new canonical UUID stock quotes for food under opening-3pct-v1 where
the guarded proposal is strictly below the baseline ask. Unit selection is common
and precedes assignment; this is eligible-quote ITT, not all incoming requests.
After assignment every task remains in its denominator even without a reply or
payment. Quotes/callbacks are not authoritative settlement evidence.

Configuration is the existing six-key Trial format at
MARKET_DIR/food_opening/seller_concession_trial.json. A fixed 1800-second window,
paired time/price-band strata, max200 total/B100 and immutable task assignments
reuse OpeningCue allocation. No flags/configs are installed with this code.
OFF without an existing assignment returns before floor computation/database writes.

Adopted defaults (opening3%, late10%, exact accept/counter) are common to both arms.
A different active seller experiment blocks new enrollment. A current grade-trial
assignment also blocks it. When this policy itself is adopted it remains common
during subsequent experiments; disabled only blocks new assignments, not sticky ones.
Old immutable snapshot changes fail A with contamination rather than repricing.

The first response's text, metadata.ask and history use the same final ask.
The assignment stores baseline_price, baseline_floor and candidate_price; quote.price
is the actual A/B price. Turn events follow unchanged price guards. Original_open,
reserve, cost, unit, kind and expiry remain traceable to the first quote.
A valid B witness requires a B assignment, guarded price change and returned
requires_input log; even this is not paid evidence. Use task/unit/actual agreed
price/settle receipt and fixed600 tracking for economic metrics.

An observer must bind current producer hashes, preserve all assigned rows and
fail on gaps/conflicts/missing receipts. Unknown excluded/shared-unit economics
stay unknown. Do not reuse another trial's final.json or six-hour cohort.
After the fixed window and 600-second followup, review Players shop+buyer,
strict paid/h, contribution and safety before practical adoption. Paid+6h uses
the last actual paid timestamp, not enrollment/window end.

Rollback: create food_opening/disabled for new A; existing assignments are sticky.
Do not change another trial's flags or rollback buyer/meta files.
