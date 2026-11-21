# Official v4 contract supersedes the initial Round 3 plan

Primary source: https://market.ourolabs.ai/agent.md, preserved verbatim in
`logs/official-contract-20261031T090702Z.md`. The market API still reported Round 2
at this check. Upstream `weeks/week-08/README.md` returned 404 via GitHub.

The previous statement that this kit-v3 implementation would automatically
complete the next Round 3 no longer applies to the newly published contract.
The legacy buyer now waits without binding trades for rounds >=3. `readiness.py`
reports `round3_contract_ready: false`, independently of current Round 2 health.

| Area | Preserved implementation | New official v4 contract |
| --- | --- | --- |
| Products | lamp/chair/bookshelf | about 5,600 SKUs, category orders and acceptable SKU sets |
| Stock | no stock procurement | inventory units, replenishment, balance and limited bankruptcy |
| Cost limits | legacy fixed reserves | no floor; below-cost liquidation allowed; storage/spoilage matter |
| Quality | item-only contract | grades A/B/C, food expiry, certificates and goods breakage |
| Buyer acceptance | item/price/signature | certificate, remaining food life, grade and warranty requirements |
| Buyer reward | verified Saved | realized value minus price plus refund; Surplus changes on breakage |
| Shop reward | signed-margin proxy / settled margin | balance minus initial capital and bankruptcy injection |
| Ranking | default Saved / Profit | role ALL percentiles; Player rank sums Shop ALL and Buyer ALL |
| Feedback horizon | payment / 600-second censoring | food lifetime and goods breakage up to 6 hours, refund revisions |

Official ALL weights: Shop profit/revenue/stars 0.5/0.3/0.2; Buyer
surplus/forum votes/deals 0.5/0.3/0.2. These are weights of percentiles, not a sum
of raw credits and stars. Owner-identical shop/buyer settlement is prohibited.

## Reusable foundation

Immutable experience, identity/contract namespaces, forecasts separated from
facts, outcome critics, delayed outcome revisions, bounded experiments, token
and latency measurement, secret handling and operational logs remain useful.

## Necessary migration before enabling Round 3

1. Read/pin the official v4 kit in an authorized location; preserve the shared
   course files and the existing actor baseline. Verify its hooks with fixtures.
2. Connect `order.json`, acceptable SKUs, `shops.json.offers`, `catalog.json`,
   `inventory.json`, `payments.jsonl` and `deal.json` as typed provenance-backed inputs.
3. Use actual units/cost/grade and implement the seller receipt extension through
   the kit's authoritative path. Do not append arbitrary fields to the old signature.
4. Design and compare replenishment, food liquidation and warranty policies;
   costs and capital constraints must replace the legacy hard cost floor.
5. Replace Saved with expected/realized Surplus and add delayed break/refund
   attribution. `broken.jsonl` names may be ambiguous; keep ambiguous attribution
   unknown instead of accusing a guessed seller.
6. Evaluate real ALL/Player ranking components separately from surrogate reward
   rates. Forum posting/voting is not enabled by this implementation.
7. Add new-contract tests and paid-deal evidence, then enable the new namespace
   and remove the legacy Round 3 gate as a separately reviewed unit of work.

The new inventory/quality/warranty policies are the core experimental decisions
of the updated assignment, not a drop-in dependency upgrade. This document is
the concrete migration checklist; no synthetic v4 run or new-policy rank is claimed.
