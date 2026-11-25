# Optional-grade fallback trial — default OFF

Use only after the current exact-counter cohort has finished and its practical decision is recorded. Do not deploy or activate concurrently with that cohort.

Three complete known preference sentences are supported. All structured or quoted requests (JSON braces/arrays/double quotes), a mandatory JSON grade policy, extra unknown/strict grade qualifier, and repeated/ambiguous sentences stay unchanged. This conservative plain-text subset avoids treating even malformed/nested JSON strings as instructions. SKU, certificate and shelf requirements must remain identical. Only known A/B/C stock is considered. The original input is retained for product/category matching and warranty processing.

StockBook.pick first performs the original choice. Any successful original choice is returned unchanged, even if a fallback could be cheaper. Only when that same matching stock group has no original fit may B evaluate the optional-grade fallback through the same unfit predicate, units snapshot, rank and expiry ordering. No unit hold, warranty, price floor, certificate, stock, cash or receipt guard changes.

ITT assignment occurs BEFORE pick for every request matching the pre-inventory text predicate, including selections that fail. The full canonical UUID is the durable key; request SHA is immutable, mismatches are contaminated/effective A. Paired sticky assignments are balanced by requested-SKU hash and 5-minute arrival band, not by a post-treatment unit or price. At most200/B100 in a bounded30-minute enrollment window. Per-task600-second follow-up includes requests with no selected unit/quote. A missing quote after an explicit rejection is not a missing payment or missing enrollment.

State is MARKET_DIR/grade_preference. Existing six-key seller_concession_trial.json contract configures the future trial. No config/adopted flag means no treatment. adopted marks new eligible requests B uncapped after a useful result; disabled affects only NEW enrollment, not old sticky tasks. Historical cohort flags/denominators are not edited.

Telemetry stores enrolled/selected/rejected/quote/turn/paid, SHA and selected stock fields, not raw text. Selection eligibility, actual fallback use, public quote and strict ledger settlement are separate. Rejections from selection and subsequent warranty refusal remain ITT denominators. Calls or callbacks are not settlements.

A new grade-specific observer must join pre-pick assignments to selection/quote and strict receipt unit/task/price/order. Do not reuse a post-pick observer that drops rejected assignments or requires a quote for every task. Unknown/missing/foreign stock economics remain unknown; no zero substitution. Record actual paid+6h separately.

Common exact-accept adopted, opening3% and late10% baseline remain unchanged. Current counter treatment decision must precede the next window. Players(shop+buyer), settlements/hour and valid economics guide practical adoption; no significance gate and no causal claim from account-wide before/after changes. Source backup/disabled new-A rollback and seller-only coordinated drain/restart apply.
