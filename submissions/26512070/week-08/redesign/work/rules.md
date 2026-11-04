# Rule book

## goal

| id | status | rule | source | harness must |
|---|---|---|---|---|
| G1 | confirmed | The final score is the Players percentile (shop ALL + buyer ALL) on the board at 11/17 23:59. Every part is a percentile of a running total, so only our position against the others counts. | market agent.md; HARNESS.md 1.3 | Judge every change by the Players rank it moves, not by a raw total. |
| G2 | user | Seller and buyer are the two big axes. They run independently: separate processes, separate scores, separate counterparts. They meet only in the Players total. | user 2026-11-05 | Design, measure and change seller and buyer as two separate systems; combine only at Players. |

## seller.score

| id | status | rule | source | harness must |
|---|---|---|---|---|
| S1 | confirmed | Shop ALL = 0.5 profit + 0.3 revenue + 0.2 stars, each a percentile among shops with a deal or a purchase. No value = 0, ties count half. | market agent.md; checked against the board 11/4 11:1x (HARNESS.md 1.3); core/score.py | The pool includes shops that only bought stock: their profit is negative. |
| S2 | confirmed | Profit = balance - starting capital (- bankruptcy aid). Unsold stock counts as a loss; spoiled food, holding cost and warranty refunds too. | market agent.md (user quote 2026-11-05) | Profit is cash only. Show it next to book value (profit + stock at cost). |
| S3 | confirmed | Revenue = settled prices - refunds. | market agent.md (user quote 2026-11-05) | Judge warranty sales by revenue after refunds. |
| S4 | confirmed | Stars are pulled toward 3 while reviews are few. With thousands of reviews ours are close to the raw average. | market agent.md (user quote 2026-11-05); 2,110 reviews at 11/5 01:3x (design/harness-v5.md 0.1) | Our stars rank (33/52 at 11/5 01:47) is the real position; the harness must classify why stars are low. |
| S5 | confirmed | Honest = grade claims that matched what was shipped. Shown on the board; not a term of shop ALL. | market agent.md (user quote 2026-11-05) | Keep claims true (it feeds stars and buyer memory), but do not spend score effort on it directly. |
| S6 | observed | Restocking lowers profit and its percentile while book value stays: book = profit + stock cost exactly. 11/4 21:19 -> 21:43: profit 42.6M -> 27.1M (percentile 91 -> 85), stock 41.1M -> 57.9M, book 83.7M -> 85.0M. | memory/market/board-ours.jsonl, 11/4 21:19 - 11/5 01:50 | A profit percentile drop right after a restock is not a loss; a drop in book value is. Report both. |

## seller.strategy

| id | status | rule | source | harness must |
|---|---|---|---|---|
| S7 | user | Plan: no losing deals, build good stars, sell a lot to raise revenue, and turn it into profit later. Accepted cost: the profit percentile can dip while stock is built up (S6). | user 2026-11-05 | Sequence the round: grow revenue and stars first, then convert stock into cash before the end. |
| S8 | confirmed | Stock left at 11/17 23:59 stays a loss (S2). Stock cost was 39.0M at 11/5 01:50. | S2; memory/market/board-ours.jsonl | Needs a wind-down plan: when to stop buying, by category and shelf life, so stock is near zero at the end. |
| S9 | question | How far can the profit percentile dip before shop ALL drops more than revenue and stars gain? Depends on the other shops' profit spread. | open (user 2026-11-05) | Compute from the full board history: ALL change per unit of stock bought. |

## buyer.score

| id | status | rule | source | harness must |
|---|---|---|---|---|
| B1 | confirmed | Buyer ALL = 0.5 surplus + 0.3 votes on buyer posts + 0.1 deals + 0.1 buyer posts (hidden posts not counted), percentile among buyers with a deal. | market agent.md, 11/3; core/score.py | Surplus has the most room (rank 25/49 at 11/5 01:47). |
| B2 | confirmed | Surplus = list price of what served the order - price paid + warranty refunds. Spoiled food and broken goods without warranty give 0 or a loss; an agreement that is not paid gives 0. | market agent.md; HARNESS.md 1.3; design/harness-v5.md 0.1 | Count surplus only on paid, served items. |
| B3 | confirmed | Votes count only from agents with 5+ settled deals and a different owner. Earlier votes are kept and count once the voter reaches 5. | market agent.md; HARNESS.md 1.4.1 | Posts must be useful to active traders. |

## buyer.orders

| id | status | rule | source | harness must |
|---|---|---|---|---|
| B4 | confirmed | Each buyer has a hidden preferred category its orders come from. Ours is food: 888 of 994 orders in 6 h (89%). | market agent.md; design/harness-v5.md 1 (11/5 6 h window) | Order mix cannot be changed; gains come from converting food orders to paid deals. |
| B5 | observed | Food orders: 263 of 888 paid (30%); 280 agreed then refused at payment for shelf life; 222 refused by the shop; 114 no fitting shop. Goods: 86 of 106 paid (81%). | design/harness-v5.md 1, 6 h to 11/5 02:1x | Check hours left and grade before agreeing (the kit refuses after). |

## market

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M1 | confirmed | Round 3 runs 11/3 - 11/17 23:59. About 5,600 SKUs. An order = category + SKUs it accepts (0.5-1.5x base price) + budget; food orders add use_within_hours. | market agent.md; HARNESS.md appendix A |  |
| M7 | confirmed | A shop and a buyer with the same owner do not settle. Bankruptcy resets to 5,000,000, but the aid is taken out of profit. | market agent.md |  |
| M8 | confirmed | The kit checks the buyer's accept {cert, min_hours_left, grades, warranty_hours} before paying and writes the reason to payments.jsonl when it refuses. | scripts/market_kit.py v4; HARNESS.md appendix A | The buyer must read past refusals before the next order. |

## market.buy

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M2 | confirmed | A shop buys stock through the restock hook every 60 s: up to 10 lines, 1-20 units each; a batch above the balance is refused whole. Starting balance 5,000,000. | market agent.md; HARNESS.md appendix A | Split purchases; never let the balance block the next buy. |
| M3 | observed | Supply windows last about 10 min (agent.md says 3). | live 11/3 (memory r3-live-handoff) | Recheck against current logs before relying on it. |
| M4 | question | When and which SKUs can be bought: is supply per SKU periodic or random, and does it differ for food and goods? | open (user 2026-11-05) | Measure from restock logs: SKU, time offered, grade, cost. |
| M5 | confirmed | Cost and grade are fixed on arrival. Cost as a share of list price: A 55-75%, B 40-60%, C 25-45%. | market agent.md; HARNESS.md appendix A | Margin is per unit, not per SKU. |

## market.trust

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M6 | confirmed | Spoken grade claims are shown true or false in the ledger after settling. The certificate is signed by the market and can be shown or hidden. | market agent.md |  |

## food

| id | status | rule | source | harness must |
|---|---|---|---|---|
| F1 | confirmed | Food grade = remaining shelf life (on arrival A 25%, B 50%, C 25%) and falls with time. Past its date a unit is discarded at full cost. | market agent.md; HARNESS.md appendix A | Food stock loses value by the hour: buy what sells now, sell fast. |
| F2 | confirmed | Food orders carry use_within_hours; a buyer can refuse units with too few hours left. | market agent.md; B5 | Seller: match hours left to the order. Buyer: check hours before agreeing. |

## goods

| id | status | rule | source | harness must |
|---|---|---|---|---|
| D1 | confirmed | Goods grades on arrival A 70%, B 20%, C 10%. Holding cost 1% of cost per day. | market agent.md; HARNESS.md appendix A | Slow goods eat margin daily. |
| D2 | confirmed | Some goods break within 6 h of the sale. Inside the warranty the shop refunds the full price from its balance (107 refunds by 11/5 01:50). | market agent.md; memory/market/board-ours.jsonl | Warranty is a liability (refunds leave revenue and profit) and also what careful buyers ask for. |
| D3 | observed | Goods units carry the market certificate. | live 11/3 (memory r3-live-handoff) |  |
| D4 | question | Which goods break more (by SKU, grade, price), and does the warranty pay for itself in sales and stars? | open | Measure from broken.jsonl and settle lines. |
