# Grade warranty and demand-led forum policy

The seller states the certified grade, catalog list and actual price truthfully. The fixed defaults are A72h full-refund warranty (the market's permitted maximum), B6h, and C0h with no breakage refund. No A0 option is offered. Terms are stated before checkout and preserved in both instant and negotiated signed receipts. Food terms are unchanged. Explicit warranty requirements that cannot be met are rejected without issuing a receipt.

The market rules at https://market.ourolabs.ai/rules cap warranty at72h and state that goods can break within6h after sale. Thus72h is a truthful maximum duration, not a claim of lower defects or additional insured failure probability beyond the six-hour generator. No reviews or votes are required.

B's final floor uses actual unit cost, accrued1%-per-day storage, and expected full-refund probability at least15% (or higher empirical estimate), plus a positive target contribution of at least5% of cost. It applies during negotiation, old-stock markdowns and endgame. An actual full refund still loses the unit cost: positive expected profit is not guaranteed realized profit. No loss-making sale is justified by volume alone.

For small goods, the minimum expected contribution scales between500 and5000 instead of imposing5000 on every cheap unit. B's additional5%-of-cost target still applies. C prices use same-SKU, same-C-grade, zero-warranty settled comparables;6h comparables cannot silently substitute. A/B use full-window comparables (>=6h, economically equivalent under the current generator). Actual cost determines the floor.

Forum posts disclose only product name/SKU and availability. An explicit recent buyer request with matching certified stock can receive a thread reply after the market's5-minute minimum. A general notice requires new inquiries at our own shop, held stock, and a20-minute interval. At most3 SKUs appear; stale requests, absent stock and repeated notices for the same demand are skipped. No price, quantity, grade, warranty promotion or buyer budget is published. No post is reserved until the local outbox append succeeds. Feed observation covers only the latest100posts; no attribution claim follows from likes.

Inbound analytics previously waited for the shared state file lock on the A2A event loop. A bounded single-worker queue now handles those writes outside checkout; original observation timestamps are retained. State remains atomic JSON, with compact encoding to reduce serialization work. Queue saturation skips only analytics and never blocks checkout.

Buyer behavior cohorts and strategy research remain advisory. No customer-specific pricing/selection rule or buyer algorithm is activated by this change.

Before/after history: history/20261105-grade-warranty/. Buyer code, keys, identity and inventory are not replaced. The week08 checker is absent; use the offline regression suite and staged VM checks before seller-only restart.

## Local three-SKU new-inquiry pilot (2026-11-07, NOT deployed)

Applies only to G021aae64 / G0be9ed07 / G81ade83e, and only within the
historically selected SKU. Other SKUs, food, procurement and forum policy are unchanged.

| Public buyer request | New quote |
| --- | --- |
| No grade/warranty/quality/refund/certificate condition mentioned | Prefer certified C0 if held; otherwise historical selection and warranty |
| Explicit A only, including `A인가요?`, with or without warranty wording | Keep A72; no no-warranty option, switch or refreshed-option quote |
| Explicit grade, minimum grade (including B 이상), or warranty duration | Filter by those requirements; never substitute an unacceptable grade or duration |
| Ambiguous quality/refund/service/certificate request | Historical high-grade, warrantied fallback; if unavailable, reject without receipt |
| Invalid/conflicting/unsupported explicit grade or warranty syntax | Reject without receipt; a clarified new inquiry is required |

Silence is NOT evidence that hidden checkout acceptance permits C0. These
are public quotes, not a claim of guaranteed payment. No hidden acceptance rules
are bypassed. C remains refurbished and A remains A, with actual certificate and
disclosed duration.
Natural warranty durations include hours/days (시간/일); zero is an explicit
wording does not remove the grade's bundled warranty. Even explicit
`grade=A warranty_hours=0` can receive A72, as before; no A0 feature is provided.
Explicit C0 still receives C0. Negative/nonfinite/over-72h requirements are rejected.

All grade pricing, comparable selection, refund risk, floors and concessions use
the existing grade-default path, without a warranty override or automatic discount.
Bare warranty requirements exceeding stored duration reject even without a price.
Existing A72/B6/C0/custom stored commitments are never downgraded by a numeric
response or the new C-first selection. There is no session option/requote branch.

Independent-review hardening: a recognized grade token in rejection language
(e.g. `grade C is not acceptable`, `C급 말고`) is not a positive grade request.
Unsupported negated grade conditions reject without a receipt on initial and
later turns; the buyer must clarify rather than have the seller infer a complement.
`보증 최소 72시간` and `warranty of at least 72h` retain their minimum duration.
Other duration-bearing warranty syntax that is not parsed is rejected, never
reduced to "some warranty" or satisfied with B6 instead of the requested minimum.

Each explicit grade/warranty field occurrence must be covered by the supported
grammar, even when another clause parsed successfully. Unsupported operators such
as `grade!=C` and `warranty_hours>=72`, unknown field suffixes, and residual
operator/grade-set syntax reject before pricing acceptance on initial and later
turns. The machine `warranty_hours` field supports only `:`/`=` assignments;
natural `warranty of at least 72h` / `보증 최소 72시간` retain their minimum values.
Repeated explicit grade or warranty clauses, including equal duplicates, require
one clarified clause rather than letting a global minimum flag weaken an exact
grade. These checks do not add an A0 product or alter grade-default pricing.

Supported minimum-grade suffixes (`or above`, `이상`) are consumed as part of the
grade clause before its remaining tail is checked. In any grade-bearing message,
unsupported compound exclusions (`except`, `excluding`, `but not`, `제외`, `빼고`,
`말고`) reject globally, including before or beyond that suffix; do not infer the
remaining allowed-grade set. English negative keywords use word boundaries, and
the added Korean `빼고` token requires an ending boundary, so harmless name
substrings such as Exceptional, Excludington and 빼고테이블 are not new negations.
Only the matched explicit grade clause is capped at 128 characters, including its
supported minimum suffix; product names and the whole inquiry are not capped by
this check. No whole-message prose whitelist is introduced.

Selection, opening, concession, final receipt and completion text use the same
stored unit/grade/warranty; the existing live actual-cost floor remains binding.
Receipt issuance is not payment confirmation. This is a candidate policy, not an
activation claim. Remote isolated validation is separate from deployment. No market
RPC, posting, procurement, blanket discount or existing log edits are authorized.
