# A/B/C/D revalidation

This is offline Mock evidence only. No market, network, or LLM calls were made.

## Stage 1 — new seeds 601–620

Each strategy ran 20 seeds × 100 attempts. Seller total: 8,000; Buyer total: 8,000; combined: 16,000.

| Seller | Mean cash profit | 95% CI | Worst | Success | Revenue |
|---|---:|---:|---:|---:|---:|
| A | 197,127 | [122,821, 271,434] | -81,940 | 9.65% | 1,106,812 |
| B | 1,104,134 | [1,002,114, 1,206,154] | 759,734 | 78.45% | 7,396,761 |
| C | -340,878 | [-416,796, -264,960] | -665,462 | 14.35% | 640,509 |
| D | -414,134 | [-534,247, -294,022] | -940,667 | 31.95% | 3,061,502 |

Seller B beat A on all 20 paired seeds. Paired profit difference: +907,006, 95% CI [+795,839, +1,018,173].

| Buyer | Mean surplus | 95% CI | Worst | Success | Average price |
|---|---:|---:|---:|---:|---:|
| A | 927,611 | [787,666, 1,067,556] | 496,852 | 85.75% | 100,358 |
| B | 1,375,151 | [1,225,664, 1,524,638] | 852,386 | 88.25% | 94,704 |
| C | 1,235,708 | [1,092,321, 1,379,094] | 738,339 | 85.80% | 100,299 |
| D | 936,061 | [758,949, 1,113,173] | 287,738 | 80.95% | 104,615 |

Buyer B beat C on 14 of 20 paired seeds. Paired surplus difference: +139,443, 95% CI [+20,292, +258,595]. The unpaired strategy intervals overlap, but the paired same-seed difference is positive.

## Stage 2 — new seeds 701–730

The top strategy B ran 30 seeds × 200 attempts for each role: Seller 6,000 and Buyer 6,000, combined 12,000.

- Seller B: mean cash profit 2,746,657; 95% CI [2,622,370, 2,870,943]; worst seed 2,033,131; success 77.38%; revenue 14,450,037.
- Buyer B: mean surplus 2,869,528; 95% CI [2,713,018, 3,026,037]; worst seed 1,787,408; success 87.35%; average price 93,574.

## Interpretation

B is reproducibly strongest in this Mock configuration. This does not supersede the separate B-safe analysis: B-safe-1 remains the recommended operational Seller candidate because it preserves B-like settlement performance while limiting inventory exposure. Real demand, competitors, certificates, failures, reputation, and settlement behavior still require official-environment validation.
