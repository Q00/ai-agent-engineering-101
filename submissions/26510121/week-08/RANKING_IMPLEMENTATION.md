# Ranking strategy correction

Source: `RANKING_STRATEGY.md`; HTML R3 / WATCH / HW ‘값 다시 정하기’.
User authorized implementation on 2026-10-27. Baseline is commit `8b21a8e`.
Pricing decisions remain model-driven; this implements evidence and experiments,
not an assertion that the assignment's pricing question or first place is solved.

| Strategy requirement | Implementation target | Initial status |
| --- | --- | --- |
| Identity, item, budget band, Round | Public-key-aware durable buyer context | Pending |
| Saved per order cycle time | Separate final feedback, failure and polling/checkout time | Pending |
| Useful counteroffers and stopping | Model context with price/latency evidence and bounded turns | Pending |
| New and stale counterparties | Deterministic, adaptive exploration | Pending |
| Checkout availability | Cached public ACP read before binding negotiation in R3 | Pending |
| Seller quote experiments | Task-stable arms with preserved memory-v1 control | Pending |
| Settled margin and censored opportunities | Per-arm, per-Round outcome evaluation | Pending |
| Private-safe shared facts | Actual transaction prices and capability only | Pending |
| Cost and latency | Worker, API, peer, order-cycle and token measurements | Pending |
| Continuous operation | Reuse identities, official v3 kit, test and restart | Pending |

All runtime DB changes are additive events. Original logs are not edited.
An online SQLite backup is under ignored `.market/backups/`; baseline source
hashes are in the new `logs/ranking-baseline-*` record. No credential is included.
