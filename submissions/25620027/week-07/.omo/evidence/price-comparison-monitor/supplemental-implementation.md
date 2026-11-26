# Supplemental quote parsing — implementation evidence

Scope: only `market_a2a/price_probe.py` and `tests/test_price_monitor.py` changed in this follow-up. No extra external request, runtime restart, deployment, or Git operation occurred. The preceding separately authorized diagnostic made exactly two openings and two cancellation attempts, recorded in diagnostic-quotes.json; those are not repeated here.

The new supplemental_ask accepts only the exact requested basic SKU prefix and either (1) the complete `<item>: asking price <integer>` form or (2) a single bounded advertisement followed by the exact terminal `Asking <integer>. Send your offer as a number.` form. Full matching rejects extra trailing instructions/newlines. Advertisement numbers are never interpreted as prices. Values must be positive integers of at most nine digits. Only the numeric result enters an ObservedQuote. Advertisements and their review promises are not forwarded or fulfilled.

opening_quote first applies existing explicit item/SKU identity guards, then uses existing Task.asking_price. Supplemental parsing runs only if the existing parser returned None, preserving valid structured metadata precedence. An explicit variant or other product still blocks the quote before fallback parsing.

## Captured checks

Cached binary directory: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin`.

- `python -m pytest tests/test_price_monitor.py -v`: supplemental-tests.txt — **48 passed**.
- `ruff check market_a2a/price_probe.py tests/test_price_monitor.py`: supplemental-ruff.txt — **All checks passed**.
- `basedpyright --pythonpath <cached directory>/python market_a2a/price_probe.py tests/test_price_monitor.py`: supplemental-types.txt — **0 errors, 0 warnings, 0 notes**.

Scenarios and binary observables:

- `test_supplemental_quote_when_format_varies_returns_only_bound_price` (15 cases): known forms yield115/110; wrong item, variant prefix, ad numbers only, impersonation-only ad, partial suffix, appended instruction/newline, zero/negative/fractional/overlong numeric forms all yieldNone.
- `test_refresh_when_supported_text_has_no_metadata_observes_only_price` (2 cases): actual Remote HTTP parsing plus monitor produce numeric prices for all three product requests;15 opening requests and15 cancellation attempts recorded by HTTP fake; ad prose absent from snapshot.
- `test_refresh_when_metadata_and_supplemental_text_conflict_metadata_wins`: metadata150 beats text115.
- `test_refresh_when_supplemental_price_has_explicit_variant_rejects`: variant SKU yields product_mismatch despite parseable text.
- Prior29 monitor cases run in the same48-case invocation: identity mismatch, URL safety, bounded requests/cancellation, cache, outage behavior and lifecycle cap remain passing.

No official25 or live re-query is claimed. The parent controls subsequent deployment and periodic collection. This is deterministic extraction of a seller's claimed initial ask, not verification of inventory, final price, coupon eligibility or truthfulness.

## Source hashes

- `market_a2a/price_probe.py`: `d01550252ce778306fe509139303ea153d3affd08afa5abfa61a2d3ff80171be`
- `tests/test_price_monitor.py`: `5a29dc9af6e5e5ce30feea131c549d2e18b29eca2bca6ebdbb35cab343b7a2d3`
