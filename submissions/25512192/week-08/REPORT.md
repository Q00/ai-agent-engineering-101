# Week 08 (midterm) report: my shop and buyer in Round 3

Player `winun1127` (student 25512192). Shop and buyer both run from this folder with kit v4
(`scripts/market_kit.py`, not modified) on a Linux server since 2026-11-03 21:39 (`join.sh`, commit `2f5c19e`).
Architecture figure: `architecture.pdf` (source `architecture.tex`).

## 1. The three parts

### Model: two models on two clocks

| where | model | what it decides | what code decides instead |
|---|---|---|---|
| `seller.py` counteroffers | haiku (`harness.MODEL`) | accept / counter / reject a buyer's offer | the floor and the opening ask (`stock.terms`), never below the floor, never above its own ask |
| `market_buyer.py` pick + `buyer.negotiate` | haiku | which of the top candidates to buy, every offer | the candidate list and its expected surplus, the price cap `min(budget, list x max_paid_ratio)`, the accept checks |
| `strategist.py` | sonnet (`harness.STRATEGY_MODEL`) | every number in `state/policy.json`, with a reason | `policy.clamp()` bounds every field; a broken answer falls back to the last policy |

All calls go through `harness.py`: Claude Code headless, `--tools ""`, no settings, empty temp directory, one JSON
answer. Haiku is on the fast clock (one order, one buyer, seconds), so it only makes judgement calls between options
code has already priced. Sonnet is on the slow clock (every 30 min), never trades, and only changes numbers.

### Context: one layer shared by shop and buyer, plus a meta-harness

- **`market_memory.py` → `state/market_stats.json`.** The public ledger is the only place that says what sells.
  Every Round 3 settle is folded into counters per category, SKU and shop (count, paid/list ratios, grade claims
  true/false, volume per hour). Nothing raw is kept: 452,711 settles fit in 0.7 MB. Restock, the seller's ask,
  the buyer's expected price and the forum all read this one file.
- **`buyer_memory.json`.** Our own trades keyed by shop name. The kit's `payments.jsonl` (paid, or why not) and
  `broken.jsonl` are folded in, and `review.py` adds the settled facts (was the grade claim true). The buyer
  trusts its own history with a shop before the ledger's average for it.
- **`state/sales.jsonl`.** Our shop's deals with cost, price and claim.
- **Meta-harness: `strategist.py`.** Every 30 minutes sonnet gets one page of evidence (ledger stats, our stock,
  our sales, our buyer's payments and breakages) and the current policy, and answers with a full new policy and a
  paragraph of reasons. 314 runs between 11-03 21:04 and 11-10 18:58, each in `state/policy_history.jsonl`
  with its evidence. What it changed most: `buyer.offer_open_ratio` (142 runs), `shop.open_ratio` (118),
  `buyer.max_paid_ratio` (51), `shop.floor_margin` (18), `shop.units_per_sku` (15), `shop.stock_categories` (12).

### Tools

The kit calls three hooks; I wrote all three, and two more modules the hooks use.

- **`restock.py`** (every 60 s, code only): categories from the policy or the ledger's best sellers; SKUs are the
  ledger's best sellers, then the SKUs priced nearest the category median (orders accept 0.5–1.5x a reference
  price, so the median fits most orders); food only if it lasts `food_min_shelf_hours`; stock at cost at most
  `max_stock_share` of the balance. It also drives memory sync, the strategist and the forum, so no extra process.
- **`stock.py`**: `find_unit` (the unit for the SKU the buyer named) and `terms` (floor, ask, grade claim,
  warranty). The claim is the certified grade, with 15 minutes of slack before a food grade cut.
- **`review.py`** (after each paid deal): stars from the settled facts, not from what the shop said. False claim
  1, food that will not last 2, then 3–5 by paid/list.
- **`forum.py`**: the buyer's market report, built in code from the stats and citing ledger seqs, so it cannot say
  anything the ledger does not show.
- **The buyer's `accept` line**: certificate required, food must last `use_within_hours` + margin, goods only in
  the accepted grades and with a warranty when the policy asks. The kit checks it before paying.

## 2. The architecture figure, and how it differs from the kit default

`architecture.pdf` shows the kit and the market at the top (unchanged), the shared context layer in the middle
with the strategist's slow loop (evidence → sonnet → policy → logged), and the shop and buyer fast loops at the
bottom. Shaded boxes are what differs from the kit's default agent:

- The default `seller_r3.py` sells from stock at no less than cost, makes no grade claim and gives no warranty. Mine
  prices from the market's median paid/list for the category, claims the certified grade (2,285 of 2,289 claims
  true on the board) and gives a warranty per grade.
- The default restock does not read the ledger. Mine stocks the ledger's best sellers in categories the
  strategist picks.
- The default buyer pays through a fixed checkout. Mine ranks every acceptable (shop, SKU) by expected surplus
  from memory, and refuses checkouts that fail its policy.
- Reviews and forum posts come from settled facts, and every policy change is logged with its evidence.

## 3. Round 3 results (at submission time; the final rank is the board at 11-17 23:59)

Board, 2026-11-10 19:15 (`https://market.ourolabs.ai/api/board`):

| | settled deals | other |
|---|---|---|
| shop | 2,289 | revenue 158,527,386, profit 19,703,999, book 29,191,748, 4.51 stars, 40 refunds of 76 broken, ALL 0.463 |
| buyer | 6,146 of 22,066 orders | saved 0.291, forum rating 0.926 on 477 posts, ALL 0.670 |
| Players | total 1.133 | 31st of 46 students (student_pct 0.333) |

Both are over the 20 settled deals of the base condition. In `logs/market-kit.log` a settled purchase reads:

```
04:25:49 [acp] paid 7611 through https://called-lemon-trail-symptoms.trycloudflare.com/acp, settled at ledger #4590930: ...
```

**Why the buyer did not pay** (`.market/payments.jsonl`, 13,946 checkouts; 6,149 paid). The biggest groups:
shelf life under the policy margin (about 4,000 in all, e.g.
`not paying: 10.82 h of shelf life left, the policy asks for 12.0`), `grade C, the policy accepts ['A', 'B']` (865),
`warranty 0 h, the policy asks for 6` (526 for all short warranties), and shops' own failures such as
`checkout refused: 409 {"error":"unit u-1c668c9c8fac is not in stock for this order"}`.

**What went wrong with the shop.** Sales per day from `state/sales.jsonl`:
11-03 356, 11-04 1,658, 11-05 406, 11-06 436, 11-07 89, 11-08 0, 11-09 1, 11-10 0. From 04:26 to 19:17 on 11-10
the seller answered all 2,371 requests with `Sorry, we have none of that in stock`, and 2,336 of them named a SKU
that was in `.market/inventory.json`. Running `stock.find_unit` on the latest request in a fresh process found
the unit. So the live seller had every unit marked as taken: `self.open` kept each negotiation until it closed,
and a buyer that leaves at `input-required` without `CancelTask` never closes it. Over a week, abandoned talks
reserved all 105 units. Because nothing sold, stock at cost (9.49M) stayed over `max_stock_share` x balance,
so restock logged `105 units in stock, room -3311749 after buying nothing` every minute and bought nothing.

The strategist saw "no sales in the last 6 h" and lowered the floor. It could not find the cause, because its
evidence page has no count of the seller's rejects. That is a gap in what I put in its context, not in the model.
Fix (commit `0535942`): a negotiation idle for 10 minutes no longer holds its unit. Claude found this from the
logs while reviewing my submission with me; I approved the fix.

## 4. Process

The steps are in the commit history from `2ef9b5b` (copy of week 07) onward, failed attempts included: two failed
Linux joins (`python: not found`, then `No module named a2a`, commits `ae10422` and `2e7136c`), and the stalled shop
above. The kit log rotates at 20 MB, so `logs/market-kit.log` and `logs/market-kit.log.1` hold only the latest part
of Round 3; the earlier days are in `state/` (sales, policy history) and the ledger.
