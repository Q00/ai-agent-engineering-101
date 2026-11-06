# Restock step 1: revert judgment and step 2 material (2026-11-06 20:50 KST)

Judged on data up to the restart of 18:32:47, as instructed. No setting changed. Unit costs and stock cost stay out of this file. Payments are counted by `status == "completed"`.

## Revert rule: not met, step 1 stays

| per hour | base 10:47-14:27 | 14:46-18:32:47 | 16:47-18:32:47 (stock 60+) | reference: since restart, cap on |
|---|---|---|---|---|
| hours | 3.67 | 3.78 | 1.76 | 2.30 |
| deals | 37.6 | 57.4 | 60.7 | 73.5 |
| Book | 3,565,445 | 3,102,055 | 2,965,080 | 2,268,627 |
| revenue | 15,116,494 | 11,780,077 | 11,439,887 | 12,159,482 |
| Book, sales under 1M | 960,983 | 1,298,264 | 1,037,653 | 911,416 |
| sales of 1M or more | 24 | 16 | 8 | 10 |

- Book below base: yes. Revenue below base: yes. Book under 1M not above about 960,000: **no**, it is above in every window before the restart. The rule needs all three.
- The restart came before four hours at stock 60+ passed, so the rule's own period has at most 2.11 h of data before the cap.
- Alive shops 54-55 on the board reads, kit minimum 50 before the restart: never under 40.
- The reference column meets all three, but the cap changes prices there, so it does not count.

## 1. Sales of 1M or more: not caused by stock mix

| list 1M+ | before | after |
|---|---|---|
| supply, units/h | 5.7 | 5.6 |
| share of units supplied | 14.8% | 8.0% |
| SKUs supplied | 5 | 4 |
| SKUs sold | 7 | 5 |
| stock, median (min) units | 4 (2) | 5 (3) |
| share of stock | 22.3% | 10.8% |
| our sales/h | 6.5 | 4.8 |
| market sales/h | 74.7 | 70.6 |

Count did not fall and stock never reached 0; the share fell only because other goods were added. The range is narrow: 21 of 23 proven 1M+ SKUs now fail the plan's test (market median at or under 0.75 of list × surge × 1.01). That test assumes a worst cost above what supply usually costs. It was already so before step 1, so step 1 did not cause it. The fall is about 2 sales an hour on 16 to 24 sales; variance and few SKUs are not separated.

## 2. What holds stock under 80

- Since 16:47 stock is 50-80, median about 63-67; 80 was reached once in 49 samples. Peaks follow each window, then sales take it down.
- Supply 69.6 units/h. Sales 57.4/h before the restart, 73.5/h after. At that pace, supply no longer exceeds sales.
- Runs since 14:46: 294; 247 window closed, 46 bought, 1 stock or cash limit. The usual buying run is at its limit: 6 lines in 38 of 46, 12 units in 25 of 46.
- Budget is not binding: estimate is 0.29 of budget.
- Candidates are close to binding: 26 under 1M and 2 of 1M+ pass, a ceiling of 74 at 3 per SKU.

## 3. Step 2 (proposal only)

Replay of the plan's own picks with no sales, in memory:

| change | runs | units | SKUs | depth | 1M+ units (SKUs) |
|---|---|---|---|---|---|
| now | 1 | 80 | 40 | 2.00 | 5 (3) |
| total 160 alone | - | no change on a run | - | - | - |
| total 160, lines 10, run 30 | 2 | 93 | 40 | 2.33 | 7 (3) |
| + per SKU 6 | 6 | 159 | 40 | 3.98 | 13 |
| total 160, lines 10, run 30, test factor .65 | 7 | 160 | 93 | 1.72 | 15 (13) |

The same factor .65 replay at 19:01 gave 114 SKUs and 21 1M+ SKUs, so these figures move with the market.

- Values: `restock_capacity.json`, and bounds in `restock_capacity.py` (read by the short hook, no restart). Factor: `restock_policy.py:139`.
- Time (estimate from 2-3 runs per window, 73/h sales): per SKU 6 about 1.5-5 h. Factor .65 at least 2 h, and it may not reach 160.
- Limit: at factor .65 the added SKUs sell near .63-.72 of list, and since the cap our sales under 1M pay .753. Book per A sale under 1M already fell from 18,949 to 9,250.
- More stock raising sales is not shown. Visits and deals rose after step 1 with more SKUs, and now the cap overlaps.
