# Two-hour check after the patch 3.4 settings, 2026-11-06 12:46 KST

Window 10:43:35-12:45:00 KST, 2.02 h. All settings as they were, nothing changed by this check. Past the window every figure is a straight line through a small sample, not a forecast. No unit costs here.

Sources: `floor-live-20261106T1246-after-all-on.json`, `floor-live-20261106T1246-since-gate.json`, `saved-projection-20261106T1246.json`, `saved-projection-before-floor-20261106T1246.json`, `two-hour-check-20261106T1246.json`, `board-20261106T0150Z.json`, `board-20261106T1246.json`.

## Buying

| | 00:00-10:09 | 06:00-10:09 | 10:43-12:45 |
|---|---|---|---|
| Paid per hour | 19.89 | 24.79 | 11.86 |
| Mean discount, new deals | .2779 | .2811 | .3191 |
| Mean worth after faults | | .2545 | .3063 |

The first column holds 139 shop-list readings under 40, the last at 05:56; the 06:00 column and the window have none (window 41-52 shops). 11-05 at the same clock paid 22.24 an hour, before the floor.

- Under the floor 0, under the grade-required discount 0, lowest discount .25. Refused by the floor at an ask 32.
- Board deals rose by 24 between the two boards, the same as the payment log. Worth of those deals from the board: .3215.
- `seller_rejected_at_open` .1592 (before .2127). Refusal skip .7866 of orders, 10 shops (before .8124, 12).
- Orders per hour stayed level (155 to 161), paid per order fell from .128 to .0736. Paid by clock hour: 09h 23, 10h 16, 11h 8, 12h 13 in 45 minutes. The window and the floor are not separated from the market by any control group.

## Agreed but not paid

Our buyer agreed 34 deals and the kit refused to pay 10 of them (.294; before .086, 19 of 221): shelf life 6, grade 3, other 1. Nine came from one shop. The check worked as meant. The cost is the order. Why the share rose is not established.

## Price reading

| Expression | Deals | Paid | Receipt mismatch | Ask per list |
|---|---|---|---|---|
| `my_price` | 9 | 0 | 0 | .70-.86 (4 read) |
| `metadata_ask` | 7 | 0 | 0 | .75-.84 (7 read) |
| `colon_krw` | 1 | 1, at .28 | 0 | |
| existing parser | 295 | 23, at .3208 | 0 | |

`group_b_lowest_price` did not fire. Too few to judge an effect on discount. No outcome of `receipt_mismatch` in the window, so no expression was turned off.

## Board

| | 10:47 | 12:46 |
|---|---|---|
| Saved | .2436 | .245 |
| Forum | .819 | .847 |
| Posts | 23 | 33 |
| Deals | 1,211 | 1,235 |
| `student_buyer` | .408 | .48 |
| `student_total` | .94 | 1.042 |
| `student_pct` | .39 | .405 |
| Student rank | 26 of 42 | 26 of 43 |

Student percentiles now: Saved .634, forum .39, deals .268, posts .195. Students' median pace between the boards: 47.4 deals an hour. No `logs/forum-buyer-stop.jsonl`. Own buyer posts in the buffer since 10:43: 11, likes 77, dislikes 11.

## End of round estimates (275.2 h left)

Posts at 6 an hour, forum held. "Still" holds rivals where they are; "at pace" runs each student at their pace between the two boards (1.98 h, noisy).

| Case | Deals | Saved | Buyer, still | Buyer, at pace |
|---|---|---|---|---|
| Floor, no retry | 4,499 | .2885 | .7244 | .5951 |
| Retry at half the estimate | 5,221 | .2861 | .7463 | .5951 |
| Retry at the full estimate | 5,944 | .2842 | .7512 | .6000 |
| Pace before the floor (06:00-10:09) | 8,057 | .2529 | .6829 | .4707 |
| Floor, posts at 12 an hour | 4,499 | .2885 | | .6244 |

Retry estimate: 138 of 147 lost orders had another candidate, times the window's paid share of tries (.077), so 5.25 an hour, worth .2707 with grade unknown.

## One-unit listings

Refused at open by listed quantity: 1 unit 14 of 34 (.412), 2-3 units 16 of 45 (.356), 4+ units 20 of 233 (.086). Of the 34, 18 had an eligible candidate with two or more units. Moving them gives up .0518 median (.0671 mean) of expected discount and avoids about 3.2 of the 14 refusals in the window. At the .092 paid share of tries not refused, that gains about .09 of Saved total and loses about .11 in two hours, roughly nothing. The gap sits at four units, not at one; n is small.

## Supply against surge

26 of 1,648 supplies now sit above .75 of list, 25 reported earlier plus one. All are goods, from 2 SKUs; 25 are from a SKU on the current surge list. The lowest factor their price implies is median 1.076, max 1.181 (about 88 and 140 market units in the hour), none above the rule's top of 1.5. The current list has 13 SKUs, factors 1.004-1.14, all fitting 1 + .002 per unit over 50; 1.174 was read earlier today. This fits surge but does not prove it: past lists were not saved and the ledger has no supply rows. No held unit is on the current list. `restock_policy.py` still uses a fixed .75, unchanged as instructed.

## Food trial

| | First | v2 |
|---|---|---|
| Planned | 4 | 4 |
| Arrived | 4 | 3 |
| Sold | 4 | 3 |
| Discarded | 0 | 0 |
| Past 24 h, sold within 24 h | 4, 4 | 3, 3 |
| Longest hours to sale | .38 | .04 |
| Profit per deal against goods median | .328 | .656 |
| Stop signals | none | none |

Stop rules: any discard, under half sold within 24 h, profit under a quarter of the goods median. Nothing is left to wait for on the three arrived v2 units. `G32bf9a8e` never arrived and the plan blocks rebuying it. The code has no pass verdict: `analyze_food_trial.py` only reports stop signals, and widening is a decision.

Stock now: 20 goods, 0 food. Reading: stock, not price, on the evidence so far. Food leaves within minutes at .813-1.0 of list and `restock_policy` defers food outside the capped trial, while food is .516 of market settles. The sample is 7 units; price at scale is untested, and the market's kimchi median is .72 of list, processed meat .655.
