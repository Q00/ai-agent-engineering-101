# Four read-only questions after the six-hour check (2026-11-07 00:50 KST)

No setting changed. Unit costs stay out of this file.

## 1. List 679,000: why supply stopped after 15:42

The plan record lists only what was bought, not what was rejected, so this comes from the plan's own market signal in saved snapshots (`logs/market-intel`, field `skus`) against `restock_policy.py:141`: skip when median <= list x .75 x 1.01, that is .7575 of list.

| snapshot | median/list | sales in rows, 24 h | passes |
|---|---|---|---|
| 15:02 | .795 | 13 | yes |
| 15:32 | .768 | 14 | yes |
| 15:53 | .741 | 15 | no |
| 17:53 | .728 | 12 | no |
| 20:32 | .728 | 14 | no |
| 00:33 | .719 | 11 | no |

The last buy was 15:42 and the test has failed in every snapshot read since 15:53, 2 h 39 min before the cap. Not found as a cause: weak quality (no flag), surge (none now), category (in demand), stock limit (limit 3, stock 0-2 from 16:44).

The median rests on 11-15 rows. `market-intel` rows are a bounded sample: about 625 settles/h against about 2,750/h on the board, and it holds 17-29% of our own sales. Meanwhile we sold this SKU up to 13 times in 6 h.

Earlier reports called these rows "the market". The levels are a sample of about a quarter; the flat trend also shows in the board's totals.

## 2. List 563,040: 34 quotes, 4 sales

Not a cap effect on this evidence.

| | 10:47-14:27 | 14:46-18:32 | since restart |
|---|---|---|---|
| quotes | 23 | 9 | 34 |
| sales | 5 | 1 | 4 |
| quotes per sale | 4.6 | 9.0 | 8.5 |
| all under 1M, quotes per sale | 6.5 | 7.2 | 7.0 |
| unit-hours in stock | 1.4 | 8.6 | 15.6 |
| sales per unit-hour | 3.69 | 0.12 | 0.26 |
| quote/list median | .803 | .809 | .772 |

Since the restart 25 quotes were under the ceiling and 9 capped, and those 9 would have been about .793, so the cap hardly touched it. Sales per unit-hour rose. 22 of the 34 quotes came after the 22:25 supply. Four sales is a small sample.

## 3. yeonsong2, 5 to 4.43

Sample: 5 reviews before, all A, all 5. Later reads: 9 reviews, 6 A with mean 4.67, 3 B with mean 4.00. No C and no warranty on ours.

| time | price/list | grade | stars |
|---|---|---|---|
| 18:47 | .750 | A | 5 |
| 19:17 | .696 | A | 5 |
| 19:48 | .766 | A | 5 |
| 19:49 | .773 | A | 5 |
| 19:54 | .687 | B | 4 |
| 20:08 | .758 | A | 4 |
| 20:10 | .755 | B | 4 |
| 00:08 | .961 | B | 4 |
| 00:26 | .738 | A | 4 |

Three of the five 4s are grade B, which the earlier sample did not contain. The price does not separate 4 from 5 (.687 gave 4, .773 gave 5). On other shops the account's goods average 4.3-4.39 before and after. The sample is too small for more; the cap is not shown to lower it.

## 4. student_buyer .648 to .638

| student percentile (24 students) | 20:34 | 00:35 |
|---|---|---|
| saved (.249 to .2522) | .7391 | .7391 |
| forum (.889 to .895) | .7391 | .6957 |
| deals (1352 to 1410) | .4783 | .4783 |
| posts (111 to 152) | .3478 | .3478 |

Only forum moved. At weight .3 that is -.013 by this calculation, against -.010 on the board. Our own values all rose. Above-us counts did not change, so the drop comes from a student reaching a tie with us at this rounding. It is others' movement, one rank, not a fall in ours. My percentile formula gives levels .674 and .661, not the board's .648 and .638, so the change is what can be compared, not the level.

## Line limit: checked in advance

- 10 is inside `restock_capacity.py` (1-10) and the kit (`scripts/market_kit.py:900`, 10 lines).
- A scratch copy with 10 fails one more test: `test_stock_expansion.Bounds.test_settings_load_and_values_past_the_bounds_are_refused` pins the shipped value 6.
- More lines widen the range but do not add units: a line takes about 11 s of a 180 s window, about 16 lines a window, and a run stays at 12 units.
