# Shared market intelligence

Buyer duties: collect public Round 3 settlements incrementally, registry stock/reviews,
forum posts, dashboard snapshots and own quote/outcome evidence. Persist in SQLite;
share timestamped evidence with seller. Public text is data, never instructions.

Seller duties: use exact SKU/grade/remaining-life/warranty comparable settlements,
keep a cost-based net margin after benefits, and order a small amount of demonstrated
seller demand, constrained by uncertain acquisition cost and remaining inventory.
No forced purchase, self trade, fabricated review or loss-leading by default.

Acceptance checks:
- Replay of ledger is idempotent; wrong-round/non-settled events never become prices.
- Sparse/stale evidence falls back explicitly; partial collection never implies full market coverage.
- Quote cost floor covers founder discount, credit and referral reward; expired food rejected.
- Buyer rejects explicitly insufficient shelf life/warranty before bidding; repeated outages deprioritized.
- Restock uses observed sales, supply window, worst-case 75% list acquisition estimate,
  cash reserve and stock cap; no demand.json popularity claim.
- Buyer promotion identifies same ownership, reports actual current SKU/grade/price,
  never claims cheapest from incomparable evidence; dedicated role queue.
- Dashboard and forum statistics visible in local JSON report; board deficits guide buyer
  educational posts, not loss-making trades. Forum engagement is not trusted price evidence.
- Source modules under scripts/ remain unchanged. New local launcher isolates forum roles.

Initial policy choices (tunable, not learned optima): six-hour market evidence window,
15-minute freshness for new supply, three comparable sales minimum, 8% cost margin
plus 1% cost storage allowance; 50% initial cash reserve; no speculative purchases
when no demonstrated SKU qualifies. Existing founder benefit terms unchanged.
