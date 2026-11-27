# Grade warranty and demand-led forum policy

The seller states the certified grade, catalog list and actual price truthfully. A goods have72h full-refund warranty (the market's permitted maximum); B goods have6h; C refurbished goods have0h and no breakage refund. Terms are stated before checkout and preserved in both instant and negotiated signed receipts. Food terms are unchanged. Explicit warranty requirements that cannot be met are rejected without issuing a receipt.

The market rules at https://market.ourolabs.ai/rules cap warranty at72h and state that goods can break within6h after sale. Thus72h is a truthful maximum duration, not a claim of lower defects or additional insured failure probability beyond the six-hour generator. No reviews or votes are required.

B's final floor uses actual unit cost, accrued1%-per-day storage, and expected full-refund probability at least15% (or higher empirical estimate), plus a positive target contribution of at least5% of cost. It applies during negotiation, old-stock markdowns and endgame. An actual full refund still loses the unit cost: positive expected profit is not guaranteed realized profit. No loss-making sale is justified by volume alone.

For small goods, the minimum expected contribution scales between500 and5000 instead of imposing5000 on every cheap unit. B's additional5%-of-cost target still applies. C prices use same-SKU, same-C-grade, zero-warranty settled comparables;6h comparables cannot silently substitute. A/B use full-window comparables (>=6h, economically equivalent under the current generator). Actual cost determines the floor.

Forum posts disclose only product name/SKU and availability. An explicit recent buyer request with matching certified stock can receive a thread reply after the market's5-minute minimum. A general notice requires new inquiries at our own shop, held stock, and a20-minute interval. At most3 SKUs appear; stale requests, absent stock and repeated notices for the same demand are skipped. No price, quantity, grade, warranty promotion or buyer budget is published. No post is reserved until the local outbox append succeeds. Feed observation covers only the latest100posts; no attribution claim follows from likes.

Inbound analytics previously waited for the shared state file lock on the A2A event loop. A bounded single-worker queue now handles those writes outside checkout; original observation timestamps are retained. State remains atomic JSON, with compact encoding to reduce serialization work. Queue saturation skips only analytics and never blocks checkout.

Buyer behavior cohorts and strategy research remain advisory. No customer-specific pricing/selection rule or buyer algorithm is activated by this change.

Before/after history: history/20261105-grade-warranty/. Buyer code, keys, identity and inventory are not replaced. The week08 checker is absent; use the offline regression suite and staged VM checks before seller-only restart.
