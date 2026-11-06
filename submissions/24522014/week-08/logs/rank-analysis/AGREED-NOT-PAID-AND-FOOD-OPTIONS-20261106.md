# Agreed but not paid, and options for a larger food trial, 2026-11-06

Read-only, nothing changed. Sources: `agreed-not-paid-20261106.json`, `food-scale-inputs-20261106.json`, `two-hour-check-20261106T1246.json`. No unit cost here. A later run counts later deals, so 10:09-13:00 here differs slightly from the 12:45 report.

## Agreed but not paid

| Period | Agreed | Not paid | Top shop tries/h | Top shop agreed/h | Top shop not paid | Other shops agreed/h | Other shops not paid |
|---|---|---|---|---|---|---|---|
| 11-05 | 479 | .121 | 3.54 | 2.12 | .608 | 17.83 | .063 |
| 11-06 00:00-06:00 | 113 | .124 | 3.00 | 1.17 | .857 | 17.67 | .075 |
| 11-06 06:00-10:09 | 108 | .046 | 1.44 | .96 | .25 | 25.03 | .038 |
| 11-06 10:09-13:00 | 48 | .25 | 5.98 | 4.57 | .846 | 12.30 | .029 |

- **The shop did not change.** One paid and one unpaid exchange were read side by side: same reply shape, "asking N" with no grade or shelf life, a deal at our counter, and a receipt. The asks seen for its two main SKUs were the same as earlier ones. It has been refused at .608 since 11-05.
- **The mix changed.** After the floor, other shops' agreed deals fell from 25.03 to 12.30 an hour while this shop's rose from .96 to 4.57. Its asks sit just over our cap and it accepts the counter at the floor, so its deals still pass. Selection changed in the same step (ranking by expected discount), and the two are not separated. 06:00-10:09 was also a low point for this shop, so .086 or .046 flatters the comparison; 11-05 was .121.
- **Price reading is not the cause.** The timing overlaps (first refusal 10:44:02, reading on 10:43:35), but 88 of the 89 unpaid deals since 11-05 used the existing parser. The other one (`my_price`, 12:37) was a shop-side `not in stock`, and its asks and agreed price agree.
- **No wrong blocks.** All 59 grade and shelf-life refusals since 11-05 were checked against the order: grade outside the accepted grades, hours under the order's minimum, and the minimum equal to the order's. These checks are the kit's. The buyer's text check cannot see them first because the shop states neither.

## Food: what the code allows

- `food_trial.py:21` fixes 4 units and 150,000. Above that the settings file turns the trial off, so more needs a code change.
- Each trial is one purchase round: one unit per SKU, no second buy of a planned SKU, no refill. That is the only automatic stop. `analyze_food_trial.py` reports stop signals and nothing acts on them.
- A new `trial_id` starts a new round with no code change.

## Market inputs

Food settles in the sample 366 an hour across 30 categories with an eligible SKU now (3+ settles and 3+ buyers in 6 h). Grade draw in market settles: C .18-.32. Our trials drew C in 3 of 4, then 0 of 3.

| Category | Settles/h | Shops | Paid share of list | Eligible SKUs | Worst cost, median |
|---|---|---|---|---|---|
| 김치 | 80.8 | 38 | .72 | 6 | 17,078 |
| 주스/코코아/생수 | 48.7 | 53 | .673 | 6 | 13,350 |
| 과자 | 25.9 | 39 | .697 | 5 | 10,391 |
| 즉석조리식품 | 24.3 | 26 | .739 | 5 | 17,925 |
| 찌개/탕류/장류 | 18.4 | 21 | .717 | 4 | 20,138 |

Fresh fruit (61.3 an hour) and vegetables are left out: they spoil, and nobody watches at the weekend.

## Options

| | A: new round, same bounds | B: larger round | C: refill |
|---|---|---|---|
| Units | 4 | 12 | Rolling |
| Categories | 즉석조리 1, 찌개 1, 과자 1, 주스 1 | 김치 4, 즉석조리 2, 찌개 2, 과자 2, 주스 2 | |
| Budget cap | 150,000 (.17% of balance) | 250,000 (.29%) | Not sized |
| Worst estimate at medians | 61,804 | 191,920 | |
| Code | None, settings only | Two constants and their tests | New code, stop rules must act |
| Unattended weekend | Safe: loss capped at the budget | Safe in money, but code goes in right before | No |

Stop rules unchanged: any discard, under half sold within 24 h, profit under a quarter of the goods median. Under A and B they decide whether to run another round.

Recommendation: A, after the VM move is confirmed, judged 24 hours after arrival; B only if A is clean. Either way the direct profit is small; the purpose is information. Neither shows what happens to price under steady supply, which only C could test.
