# B strategy improvement — offline Mock evidence

All figures below are seeded local simulations. They are not official Round 3 scores, stars, reputation, or percentiles. External LLM and network calls were zero.

## Baseline diagnosis

The only negative Seller-B phase-2 seed was seed 209: cash profit -210,312; revenue 3,038,592; inventory purchases 3,233,876; remaining stock cost 814,406; aggregate sale margin 619,122; storage 15,028; expiry loss 0; warranty refunds 0. The loss was therefore driven mainly by cash tied in unsold inventory plus storage, not expiry or warranty. “Discount cost” is not a separately booked official expense; its effect appears through sale margin and revenue.

Buyer B's phase-3 advantage over C was associated with both a lower average price (92,601 versus 98,563) and a higher success rate (88.2% versus 85.5%). The surplus difference was 307,081. These observational components must not be presented as a causal decomposition because shop choice changes both price and settlement opportunity.

## Base comparison — 10 paired seeds × 50 attempts

| Seller | Mean cash profit | 95% CI | Worst | Revenue | Success | Remaining stock |
|---|---:|---:|---:|---:|---:|---:|
| B-original | 249,868 | [162,329, 337,407] | 55,081 | 3,849,582 | 80.2% | 683,127 |
| B-safe-1 | 557,337 | [449,681, 664,993] | 272,199 | 3,889,510 | 80.0% | 435,584 |
| B-safe-2 | 605,909 | [511,330, 700,488] | 406,928 | 3,156,036 | 64.4% | 266,350 |

Paired B-safe-1 minus original cash profit was +307,469 (95% CI +231,024 to +383,914), remaining stock was -247,542, and success was -0.2 percentage points (95% CI -0.59 to +0.19). B-safe-2 improved cash profit by +356,041 but reduced success by 15.8 percentage points and revenue by 693,546. Thus B-safe-1 is the balanced operating candidate; B-safe-2 is a cash-preservation fallback, not the primary candidate.

| Buyer | Mean surplus | 95% CI | Success | Average price |
|---|---:|---:|---:|---:|
| B-original | 694,138 | [590,827, 797,448] | 86.4% | 94,349 |
| B-quality | 666,501 | [517,037, 815,966] | 83.6% | 95,141 |

B-quality minus original surplus was -27,636 (95% CI -126,021 to +70,749), while success fell 2.8 percentage points (95% CI -4.13 to -1.47). Quality failures were unchanged because the common safety layer had already rejected unsafe offers. The extra quality weighting did not establish an improvement in this Mock; Buyer B-original remains the performance candidate, with the common certificate/grade/budget guard retained.

## Stress evidence

Stress attempts total 2,000: the initially selected B-safe-2 and both Buyers used 1,600 attempts; B-safe-1 used the remaining 400 attempts. Seller mean cash profit by stress:

| Stress | B-original | B-safe-1 | B-safe-2 |
|---|---:|---:|---:|
| competitor discount | -793,782 | -565,036 | -383,635 |
| high break | -301,185 | -51,423 | 37,058 |
| low demand | -326,595 | -67,960 | 27,878 |
| short food | -424,588 | -151,013 | 23,324 |

Both capped policies reduce downside, but severe competitor discounting remains loss-making. B-safe-1 keeps materially more settlements than B-safe-2 (for example 74% versus 52% under high-break conditions). No policy should automatically chase a competitor below its guarded floor.

Buyer B-quality had slightly higher stress surplus in all four generated stress sets, but usually a slightly lower settlement rate. Together with the inconclusive base paired-surplus interval, this supports keeping B-original ranking for launch and treating quality weighting as a monitored experiment rather than a default.

## Recommendation and limits

- Seller candidate: B-safe-1 pricing/restock caps, with 10% per-purchase and 20% total-stock-cost caps as Mock tuning values—not official rules. Below-cost sales remain disabled.
- Buyer candidate: B-original price/negotiation policy behind the existing common safety checks. Consider a smaller quality tie-break only after real certificate and settlement-history integration tests.
- Restock stays SAFE (`{"buy":[]}`). The preview adapter cannot authorize purchases.
- Real certificate verification, receipt signatures, checkout JWT, ACP/AP2 mandates, and settlement remain official-kit responsibilities.
- Actual catalog distributions, demand, competitors, failures, forum reputation, stars, and market latency are not reproduced faithfully enough to claim real-market superiority.

Generated evidence is in `b_improvement/`, `b_improvement_stress/`, and `b_improvement_stress_safe1/`; complete attempts and seed summaries are retained as CSV, with JSON summaries.
