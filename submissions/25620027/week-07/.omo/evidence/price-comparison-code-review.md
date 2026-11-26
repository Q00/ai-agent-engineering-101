# Price comparison independent review

codeQualityStatus: BLOCK
recommendation: REQUEST_CHANGES

CRITICAL: none found.
HIGH: price_monitor.py:157-162 attributes an ask to the requested item without checking returned message/task item metadata. Reproduction by inspection: lamp request returns input-required metadata.item=chair and ask=180; current code emits ObservedQuote(item=lamp, ask=180), feeding lamp comparison and opening-price policy. Reject explicit mismatches before publishing a usable quote, preserve cancellation, and add an offline transport regression. Do not equate arbitrary peer SKU naming with our local SKU without a documented mapping; at minimum declared item contradictions must fail closed.
MEDIUM: price_monitor.py refresh selection currently does not deduplicate name/URL and excludes self by substring only. Duplicate records may probe one shop multiple times and occupy top-five slots. compare_price deduplicates names but not alternate names sharing URLs. Use stable self identity and normalized URL exclusion/dedup in selection; expose missing/unsupported slots honestly.
LOW: none raised.

Other checks: comparison.py clamps to reserve/basic10% cap, rejects stale/future evidence and excludes our variants from basic comparisons. Accept paths are not capped by opening quote rules and retain actual offer/SKU/reserve guards. seller_messages preserves signature and receipt amount. UI uses textContent, HTTPS links and distinct stale/unavailable states; no dynamic raw HTML insertion found. Monitor allows only HTTPS market/trycloudflare/workers hosts, same-host advertised endpoints, no redirects/proxy environment, and no numeric offer path. Expected HTTP/schema/cache failures are isolated. Unexpected exceptions can still escape the lifespan task group; no whole-process crash isolation claim is made. 09:00 stop is external supervision, not implemented in monitor.run; loop caps at60 and sleeps600 after work, so cadence includes request duration.

Programming/remove-ai-slops perspectives consulted in session (5.1.40). Typed price boundary and observable clamp/expiration/variant/duplicate-name tests serve real requirements. No tests executed in this read-only pass. seller_turn.py has275 lines; SIZE_OK is reasonable for one cohesive per-task negotiation state machine with shared current/offer/candidate/snapshot dispatch state. Wire messages/signing have already been extracted to seller_messages; splitting the remaining action path merely for a numeric line target would obscure validation-before-mutation ordering.

Scope: current comparison/service/monitor, seller integration/policy/turn/messages, comparison tests, UI script and staged ad. Worker still finalizing monitor; this report records the inspected pre-fix state and requires follow-up on the HIGH item. Source/API/process untouched; only this authorized report written.

## Follow-up: fixes independently inspected

Updated codeQualityStatus: CLEAR
Updated recommendation: APPROVE
Remaining blockers: none found in inspected fix scope.

Original BLOCK findings above are retained as history. HIGH resolved: price_probe.opening_quote checks both Task and Message identity item/SKU before accepting ask; any nonempty value different from requested basic item yields product_mismatch with no usable price. The finally block still cancels returned tasks. The four regression cases cover both metadata locations, item mismatch and variant SKU, with cancellation count and unavailable price assertions. This conservative SKU rule can exclude a legitimate basic product using a different vendor SKU; that reduces coverage rather than falsely claiming comparable prices, and missing slots remain visible.

MEDIUM resolved for stated duplicate/self scope: price_monitor.select_shops uses exact self-name exclusion, first registry mapping per name, seen names plus normalized URL dedup, and continues board traversal until five unique slots. Regression covers repeated names, uppercase/default-port/trailing-slash URL equivalence, similar-to-self names, and missing URLs. Missing registry entries intentionally remain unavailable slots, not fabricated quotes.

Inspected .omo/evidence/price-comparison-monitor/tests.txt:29 passed; ruff.txt:All checks passed; types.txt:0 errors/warnings/notes. These are worker-run artifacts inspected, not independently rerun tests. Typed ComparisonDocument is now the route serialization boundary. Prior reserve/baseline/expiry/receipt protections remain in source. No network or process changes performed. External first live sample and deployment validation are separate parent-owned evidence, not asserted by this approval.

## Follow-up: supplemental initial-ask parser

codeQualityStatus: CLEAR
recommendation: APPROVE
blockers: none found for the supplemental-parser change.

Inspected price_probe.py:103-149 and appended test_price_monitor cases. Both added forms require the requested basic-item prefix and fullmatch; the longer form extracts only the integer immediately after final Asking and before the exact terminal sentence. Advertising percentages and unrelated numbers are not returned. Nonpositive/fractional/overlength integers and appended text after the suffix fail the new parser. Explicit Task/Message item or SKU mismatch is checked before both existing and supplemental price parsing. A valid metadata ask wins; fallback runs only if task.asking_price returns None. Existing task-state filtering and finally cancellation remain.

Precision caveat: the bounded free-text middle section can contain advertising or instruction-like text; it is not comprehensively rejected as malicious content. The safety property is that only the selected numeric ask survives, not that every instruction-bearing quote is rejected. Additional text after the required final suffix is rejected. Existing remote.asking_price behavior is unchanged. Tests cover prose percentages, wrong-item prefixes, tail instructions, metadata precedence, explicit variant mismatch, HTTP-fake openings and cleanup. No API, process action or test rerun performed. Prior BLOCK and subsequent fix approval remain above as history.
