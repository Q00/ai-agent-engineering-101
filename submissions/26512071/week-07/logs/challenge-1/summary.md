# Challenge 1: changing the shop's prices

Made by `challenge_evidence.py` at 2026-10-21 14:25. Reserves stay at lamp 100, chair 180, bookshelf 260.

## What changed

- Before (until 2026-10-20 20:50): the harness model set every ask. It opened high (bookshelf 340, chair 234) and came down slowly, so many buyers walked away.
- After: an opening price for the first 10 deals (first ask 1.05x reserve, any offer at the reserve closes), 5 more opening-price deals whenever a new review of 3 stars or less appears for my shop, then the fair-price mode (first ask at most 1.1x, any offer at the reserve or at my ask closes, asks only go down by at least 3% of the reserve).

## Board

Before: `board-before-1020-2042.json` (Round 1, 2026-10-20 20:42). My shop was still named `kimchyoungman`.

| shop | visits | deals | share | stars | reviews | revenue / deal |
|---|---|---|---|---|---|---|
| kimchyoungman | 3 | 2 | 0.004 | 3.5 | 2 | 104.0 |

Round 1 final (`board-r1-1021-1425.json`). `kimchyoungman` is the old name, `fair-furniture` the new one from 20:59.

| shop | visits | deals | share | stars | reviews | revenue / deal |
|---|---|---|---|---|---|---|
| kimchyoungman | 11 | 5 | 0.003 | 3.1 | 10 | 103.6 |
| fair-furniture | 14 | 7 | 0.004 | 3.29 | 14 | 251.4 |

Round 2 now (`board-r2-1021-1425.json`), every deal with the new prices.

| shop | visits | deals | share | stars | reviews | revenue / deal |
|---|---|---|---|---|---|---|
| fair-furniture | 155 | 111 | 0.005 | 4.56 | 111 | 201.1 |

## My shop's own logs

Before (kit log seller lines until 20:50): 34 negotiations, 19 accepted (56% closed).

| item | deals | avg price | avg price / reserve |
|---|---|---|---|
| lamp | 6 | 114.2 | 1.142 |
| chair | 7 | 201.1 | 1.117 |
| bookshelf | 6 | 285.5 | 1.098 |

After, opening price (`sales.jsonl`, promo true, market-test rows left out): 60 deals.

| item | deals | avg price | avg price / reserve |
|---|---|---|---|
| lamp | 15 | 103.1 | 1.031 |
| chair | 19 | 185.7 | 1.032 |
| bookshelf | 26 | 262.3 | 1.009 |

After, fair-price mode (`sales.jsonl`, promo false, market-test rows left out): 61 deals.

| item | deals | avg price | avg price / reserve |
|---|---|---|---|
| lamp | 14 | 103.9 | 1.039 |
| chair | 20 | 184.4 | 1.025 |
| bookshelf | 27 | 272.2 | 1.047 |

Deals after the change by round: Round 1: 10, Round 2: 111.

Negotiation results after the change (`talks.jsonl`, market-test rows left out): deal 43, buyer canceled 12; 78% closed.
