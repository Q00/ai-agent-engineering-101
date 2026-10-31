# Ranking strategy correction

Source: `RANKING_STRATEGY.md`; HTML R3 / WATCH / HW ‘값 다시 정하기’.
User authorized implementation on 2026-10-27. Baseline is commit `8b21a8e`.
Pricing decisions remain model-driven; this implements evidence and experiments,
not an assertion that the assignment's pricing question or first place is solved.

| Strategy requirement | Implementation target | Initial status |
| --- | --- | --- |
| Identity, item, budget band, Round | `Experience.buyer_evidence`, contract namespaces | Implemented/tested |
| Saved per order cycle time | Adapter final feedback includes failed/no-receipt cycles and wait | Implemented/tested for legacy contract |
| Useful counteroffers and stopping | Model context and prompt; host bounds unchanged | Implemented |
| New and stale counterparties | `ranking.select_buyer`, adaptive 5–20% exploration | Implemented/tested |
| Checkout availability | Read-only same-host ACP preflight, bounded to two candidates | Implemented/tested; v4 migration gate takes priority |
| Seller quote experiments | Task-stable arms with preserved memory-v1 control | Implemented/tested |
| Settled margin and censored opportunities | Per-arm/cycle outcome evaluation and late critic revisions | Implemented/tested |
| Private-safe shared facts | Actual confirmed prices, without budgets/mandates | Implemented/tested |
| Cost and latency | Worker, API, peer, order-cycle timings and actual token usage | Implemented |
| Continuous operation | Reuse identities; R2 continues; new v4 R3 guarded | Deploy/verification record below |

All runtime DB changes are additive events. Original logs are not edited.
An online SQLite backup is under ignored `.market/backups/`; baseline source
hashes are in the new `logs/ranking-baseline-*` record. No credential is included.

User follow-ups added separate outcome Critics and an empirical World Model,
documented in `ACTOR_CRITIC.md`; local regression: 94 tests passed. LongMemory
review is in `LONGMEMORY_REVIEW.md`; no dependency was installed. The quality
announcement was followed by the actual v4 contract. `V4_MIGRATION.md` supersedes
the old future-R3 assumptions. v4 SKU, stock, warranty and Surplus policies are
not implemented by this legacy-contract correction.

Final verification: the latest official legacy market test passed **25/25**
(`logs/market-test-20261031T091019Z/console.log`). At the deployment check both
roles were alive in Round 2, public ACP returned HTTP 200, and the journal held
30 Buyer Critic updates and 31 Shop Critic updates. The machine-readable record
is `logs/critic-deployment-20261031T091538Z.json` (or the matching timestamped
`critic-deployment-*` file). These are live R2/pilot facts, not v4 settlement proof.
