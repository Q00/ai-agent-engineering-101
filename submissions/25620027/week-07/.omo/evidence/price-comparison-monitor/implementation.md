# Price comparison monitor — local implementation evidence

Scope: new `market_a2a/price_monitor.py`, `market_a2a/price_probe.py`, and `tests/test_price_monitor.py` only. Parent owns the comparison contract, seller integration, runtime, and external validation. No external HTTP, A2A, model calls, transactions, restart, or Git mutation was performed by this worker.

## Contract and limits

`PriceMonitor(feed, cache_path)` exposes async `refresh_once()` and `run()`. The fixed board and registry endpoints supply ranked identities and URLs. The monitor keeps the first five active competitors in board order, excluding exactly our `25620027` identity. Duplicate board/registry names and equivalent listing URLs (HTTPX normalization plus trailing slash removal) cannot consume extra sample slots; later ranked entries refill up to five. Missing URLs remain visible as unavailable entries. Each shop retains three item rows, including failures. Successful observations require INPUT_REQUIRED and a positive integral ask, using the existing Remote parser. Explicit Task or status.message metadata.item/sku must exactly match the requested basic SKU; a different item or variant SKU is product_mismatch. Peers without identity metadata remain observations of the requested product, not verified identical product evidence. Identity metadata itself is the peer's claim, not a catalog authenticity guarantee. The monitor sends only initial product requests and best-effort official CancelTask cleanup; it never sends a numeric offer, checkout, rating, review, or market result.

Only HTTPS `market.ourolabs.ai`, `*.trycloudflare.com`, and `*.workers.dev` are supported. An advertised JSONRPC endpoint must use the same host. Custom domains remain ranked with unsupported_host results. Redirects, URL credentials/query/fragment/nonstandard ports, environment proxies, and transport retries are disabled/rejected. TLS verification remains enabled. This deliberately does not implement DNS pinning: the parent selected public infrastructure allowlisting as the bounded supported scope.

At most three shop probes run concurrently, with sequential three-item openings per shop and 20-second deadlines. Maximum openings are fifteen per refresh. A valid response gets CancelTask; an unsupported cancellation is only a controlled log code. A timeout without a returned task ID cannot be canceled. Raw card, seller message, review, error, or cancellation prose is not copied into snapshots or monitor logs. Display identity fields originate from the registry and remain untrusted display data; model-facing numeric selection is owned by comparison.py.

Cache serialization is atomic. Run reuses fresh cache before its next 600-second refresh boundary and stops after at most 60 refresh attempts per monitor invocation. TTL remains 900 seconds from collection start (conservative when probes are slow). Board/registry failure preserves old snapshot and expiry; without old evidence it returns an empty already-expired snapshot and does not publish/cache invented data.

## Verification

All tests use httpx2.MockTransport at the HTTP boundary. The actual Remote parser, monitor lifecycle, cache serialization, concurrency limiter, and cancellation payload execute. The production client factory is instantiated/closed without any request. No official 25-check or live market run is claimed.

Invocation binaries:

- Python: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python`
- Ruff / basedpyright: same directory.
- CWD: this submission directory.

Commands and captured artifacts:

1. `python -m pytest tests/test_price_monitor.py -v` → `tests.txt`: **29 passed**.
2. `ruff check market_a2a/price_monitor.py market_a2a/price_probe.py tests/test_price_monitor.py` → `ruff.txt`: **All checks passed**.
3. `basedpyright --pythonpath <Python above> market_a2a/price_monitor.py market_a2a/price_probe.py tests/test_price_monitor.py` → `types.txt`: **0 errors, 0 warnings, 0 notes**.
4. `ruff format market_a2a/price_monitor.py market_a2a/price_probe.py tests/test_price_monitor.py` → terminal: **3 files left unchanged** at final verification.

| Scenario | Binary observable | Artifact |
| --- | --- | --- |
| Seven active peers plus own/offline sellers | Exactly first five competitors, 15 product-only openings, 15 cancels, concurrency <=3, three numeric UTC observations each | tests.txt: test_refresh_when_ranked_shops_exist_opens_only_fifteen_and_cancels |
| Atomic cache publication | Parsed file equals returned immutable snapshot; no temporary files remain | Same scenario |
| Unsafe/unsupported URL classes (12 cases) | Rejected before any destination request | tests.txt: test_host_when_not_supported_rejects_before_requests |
| Custom domain in ranking | Duplicate custom-domain URL retained once, three unavailable rows, only two registry GETs | tests.txt: test_refresh_when_custom_domain_is_ranked_keeps_three_unavailable_items |
| Other-host card endpoint | No opening POST, endpoint_host_mismatch rows | tests.txt: test_refresh_when_card_advertises_other_host_never_posts |
| Unknown ask / cancellation not supported | 15 openings and cleanup attempts, no price, controlled cancellation error, raw hostile prose absent | tests.txt: test_refresh_when_asks_missing_and_cancel_unsupported_records_no_prices |
| Registry unavailable with old data | Old object and expiry unchanged | tests.txt: test_refresh_when_registry_fails_preserves_old_snapshot |
| Fresh cache on restart | Cache published and sleep requested before any HTTP request | tests.txt: test_run_when_cache_is_fresh_waits_before_any_request |
| Redirect to localhost | Seven original GETs only, no redirect target request, no opening | tests.txt: test_refresh_when_card_redirects_never_follows_location |
| Unsolicited completed task | Numeric ask discarded, cleanup attempted, unexpected_task_state | tests.txt: test_refresh_when_opening_is_completed_discards_ask_and_cancels |
| One peer timeout | Three unavailable rows retained; twelve other valid prices and cancels survive | tests.txt: test_refresh_when_one_peer_times_out_other_quotes_survive |
| Initial registry failure | Empty expired result; feed remains None; no cache written | tests.txt: test_refresh_when_no_cache_and_registry_fails_returns_expired_empty |
| Persistent registry outage | Loop survives and stops after exactly 60 attempts with 600-second spacing | tests.txt: test_run_when_registry_stays_down_stops_after_sixty_refreshes |
| Production client construction | Redirects disabled and timeout20 observed without any request | tests.txt: test_production_client_has_redirects_disabled_and_twenty_second_timeout |

| Explicit item / variant SKU mismatch at either metadata location | Mismatched lamp observations absent, matching chair item observations survive; all opened tasks get cleanup | tests.txt: test_refresh_when_explicit_product_differs_discards_that_quote (4 cases) |
| Duplicate names / canonical URLs, similar own name, missing URLs | Exact own name excluded; similar name retained; duplicates skipped; missing URL rows retained; five slots filled from next ranks | tests.txt: test_selection_when_duplicates_exist_refills_unique_ranked_slots |

Review correction: after adding required URL deduplication, the original fixture with seven identities sharing the same custom URL returned one shop, causing the old five-shop assertion to fail. The expectation was changed to one because unique destination sampling is now the explicit contract. No production failure was hidden. The final complete 29-case monitor suite passed.

Module separation keeps lifecycle/registry/cache code (price_monitor.py, 176 physical lines) distinct from the public URL and A2A probe boundary (price_probe.py, 163 physical lines). This split was explicitly authorized by the parent reviewer.

ULW status returned ULW_LOOP_PLAN_MISSING, so evidence uses the assigned .omo/evidence path. Managed programming skill was applied; no reusable skill change was justified or made.

## Source hashes

- `market_a2a/price_monitor.py`: `dc68a60a5d460f08f40704a0a431c0c6a09e2b54d2928f6035344eef829977da`
- `market_a2a/price_probe.py`: `a23be1159085a78598855c28bc0afaf2d8da2f5bafc19db75a9e7fdb1a245ef4`
- `tests/test_price_monitor.py`: `d9c1cfdf57b1cf054803a5bb7517c6fd41ba52790ddc10f6ba1b03f926adff71`
