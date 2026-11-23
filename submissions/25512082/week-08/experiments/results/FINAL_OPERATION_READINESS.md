# Final strategy and operation-readiness analysis

No new trades were run for this analysis. Evidence comes from the saved 16,000-attempt A/B/C/D stage, 12,000-attempt B confirmation stage, and the separate paired B-safe experiment. Different attempt lengths are reported separately, not pooled.

## 28,000-attempt revalidation

### Seller — 20 seeds × 100 attempts

| Rank | Strategy | Cash profit | Revenue | Success | Worst | Purchases | Storage | Expiry | Warranty refunds |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | B | 1,104,134 | 7,396,761 | 78.45% | 759,734 | 6,266,292 | 26,336 | 5,588 | 0 |
| 2 | A | 197,127 | 1,106,812 | 9.65% | -81,940 | 902,224 | 7,461 | 11,348 | 0 |
| 3 | C | -340,878 | 640,509 | 14.35% | -665,462 | 828,000 | 17,123 | 0 | 136,264 |
| 4 | D | -414,134 | 3,061,502 | 31.95% | -940,667 | 2,975,422 | 23,961 | 3,471 | 476,254 |

Seller B beat A on all 20 paired seeds. Mean paired cash-profit difference was +907,006, 95% CI [+795,839, +1,018,173].

### Buyer — 20 seeds × 100 attempts

| Rank | Strategy | Purchase surplus | Success | Average price | Quality rejects | Worst |
|---:|---|---:|---:|---:|---:|---:|
| 1 | B | 1,375,151 | 88.25% | 94,704 | 25.9 | 852,386 |
| 2 | C | 1,235,708 | 85.80% | 100,299 | 25.9 | 738,339 |
| 3 | D | 936,061 | 80.95% | 104,615 | 25.9 | 287,738 |
| 4 | A | 927,611 | 85.75% | 100,358 | 25.9 | 496,852 |

Buyer B beat C on 14/20 paired seeds. Mean paired surplus difference was +139,443, 95% CI [+20,292, +258,595]. Identical quality-reject counts show that strategy ranking changed only among candidates already accepted by the common safety layer.

### B confirmation — 30 new seeds × 200 attempts

- Seller B: cash profit 2,746,657, 95% CI [2,622,370, 2,870,943], worst 2,033,131, revenue 14,450,037, success 77.38%.
- Buyer B: surplus 2,869,528, 95% CI [2,713,018, 3,026,037], worst 1,787,408, average price 93,574, success 87.35%.

The absolute values are larger than the 100-attempt stage because episodes are twice as long; they are confirmation evidence, not values to add to stage 1.

## Earlier experiment and B-safe effect

The earlier A/B/C/D experiment also ranked B first for both roles. Revalidation did not change the winner. Seller B's new 100-attempt profit is not directly comparable to the earlier 50-attempt profit, but direction, success rate, and rank are consistent.

B-safe-1 was tested separately against B-original on the same ten 50-attempt seeds:

- Mean cash profit: 557,337 versus 249,868.
- Worst seed: 272,199 versus 55,081.
- Purchases: 3,323,354 versus 3,587,058.
- Remaining stock cost: 435,584 versus 683,127.
- Storage: 8,819 versus 12,656.
- Success: 80.0% versus 80.2%.
- Paired profit improvement: +307,469, 95% CI [+231,024, +383,914].
- Paired success difference: -0.2 percentage points, 95% CI [-0.59, +0.19].

Because B-safe-1 was not part of the 28,000-attempt run, its values are not pooled with that run. Its paired evidence nevertheless supports selecting it as the risk-controlled Seller candidate.

## Final candidates

- Seller: **B-safe-1**. It retains B's competitive pricing while capping purchase exposure and disabling below-cost sales. The 10% per-purchase and 20% held-stock limits are Mock-tuned operational parameters, not official rules.
- Buyer: **B-original behind common kit safety**. It has the strongest paired surplus evidence. B-quality did not prove an improvement and reduced success in its separate paired experiment.

## Official-v4 boundary and adapters

- Seller adapter reads actual inventory units, validates SKU/cost/grade/expiry/use window, picks earliest-expiring fitting stock, and emits a guarded B-safe quote.
- Buyer adapter checks SKU and budget, rejects explicitly invalid evidence, chooses by B price policy, and emits `accept` requirements. It does not treat an advertisement as a real certificate.
- The official kit remains responsible for market-certificate signature verification, checkout JWT verification, ACP/AP2 mandates, budget enforcement, unit reservation, duplicate checkout rejection, and settlement.
- Existing Seller signing remains responsible for signed receipts. Mock settlement and Mock certificates are never accepted as real evidence.
- Restock defaults to exact `{"buy":[]}`. Dry-run exposes estimated items/cost/caps but never authorizes purchase. Catalogs without an explicit cost estimate produce no preview purchase.
- A process-local duplicate guard is prepared; durable duplicate protection must be restored from payment/history logs on restart and remains a pre-deployment integration task.

## Week 08 rubric status

- Model: current strategy is deterministic rules, not an LLM. It is honest to describe it as a policy model. If an LLM is added, use it only to propose bounded decisions while deterministic guards remain authoritative.
- Context: Mock adaptive memory and the current market Buyer shop history exist. The selected B Buyer can consume history, but durable unified history for Seller sales, payment failures, warranties, and forum reputation is not yet connected end-to-end.
- Tools: inventory selection, pricing, negotiation guards, certificate/payment verification, signing, restocking, and result storage are separated. This is the strongest implemented rubric area.
- Architecture: an updated one-page `architecture.tex/.pdf` explaining policy/context/tool boundaries is still required for submission.

## Remaining before Round 3

Connect the adapters to copies of the operating Seller/Buyer commands, replay official-shaped local fixtures through A2A, test signed receipt → ACP checkout → `accept` validation with test keys/fake responses, persist duplicate/history state, inspect real catalog/demand snapshots, and manually review restock dry-run output. Keep SAFE enabled until that review is complete.
