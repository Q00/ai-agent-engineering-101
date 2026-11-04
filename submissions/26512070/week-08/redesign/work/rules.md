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
| S2 | confirmed | Cash (`profit` on the board) = balance - starting capital - bankruptcy money. Stock counts as spent, so stock that never sells is a loss. Book = Cash + cost of unsold units; Book does not enter ALL. | agent.md A150-151; /board shop note | Profit is cash only. Show it next to book value (profit + stock at cost). |
| S3 | confirmed | Revenue = settled prices - refunds. A warranty refund takes the deal out of revenue, but its stars stay. | /board shop note; agent.md A157-158 | Judge warranty sales by revenue after refunds. |
| S4 | confirmed | Stars are pulled toward 3 while reviews are few. With thousands of reviews ours are close to the raw average. | market agent.md (user quote 2026-11-05); 2,110 reviews at 11/5 01:3x (design/harness-v5.md 0.1) | Our stars rank (33/52 at 11/5 01:47) is the real position; the harness must classify why stars are low. |
| S5 | confirmed | Honest = whether grade claims matched what was shipped (no published formula). Not part of shop ALL. The ledger also shows each claim true or false. | /board shop note; agent.md A159-160 | Keep claims true (it feeds stars and buyer memory), but do not spend score effort on it directly. |
| S6 | observed | Buying stock lowers Cash by its cost while Book stays (book = cash + stock cost, exact on our board rows). The Cash percentile drops only if the order among shops changes: with the others' cash unchanged, buying more than the cash gap to the shop just below lets at least that shop pass us. 11/4 21:19 -> 21:43: cash 42.6M -> 27.1M (percentile 91 -> 85), book 83.7M -> 85.0M. | memory/market/board-ours.jsonl, 11/4 21:19 - 11/5 01:50 | A profit percentile drop right after a restock is not a loss; a drop in book value is. Report both. |

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
| B2 | confirmed | Surplus = value - price paid + refund. Value = the SKU's list price to the order, or 0 if the food did not last use_within_hours at settle or the goods broke. So spoiled food = -price paid; broken goods under a warranty that covers the break = 0; without one = -price paid. An agreement the kit does not pay is no deal. | agent.md A225-227; /board buyer note | Count surplus only on paid, served items. |
| B3 | confirmed | Buyer forum score = votes on posts written with as: buyer. A vote counts when the voter has 5+ settled deals and a different GitHub owner; other votes are kept and count once the voter reaches 5. forum.json likes/dislikes/score hold counted votes only. | agent.md A174-176, A229-231; /board buyer note | Posts must be useful to active traders. |

## buyer.orders

| id | status | rule | source | harness must |
|---|---|---|---|---|
| B4 | confirmed | Each buyer has a hidden preferred category its orders come from. Ours is food: 888 of 994 orders in 6 h (89%). | market agent.md; design/harness-v5.md 1 (11/5 6 h window) | Order mix cannot be changed; gains come from converting food orders to paid deals. |
| B5 | observed | Food orders: 263 of 888 paid (30%); 280 agreed then refused at payment for shelf life; 222 refused by the shop; 114 no fitting shop. Goods: 86 of 106 paid (81%). | design/harness-v5.md 1, 6 h to 11/5 02:1x | Check hours left and grade before agreeing (the kit refuses after). |
| M16 | confirmed | Ask a shop for one of the SKUs it has in shops.json offers (for example 'I want to buy <name> (<sku>)'); an acceptable SKU is not necessarily in every shop. | agent.md A132, A195 |  |

## market

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M1 | confirmed | Round 3 runs 11/3 - 11/17 23:59. About 5,600 SKUs. An order = category + SKUs it accepts (0.5-1.5x base price) + budget; food orders add use_within_hours. | market agent.md; HARNESS.md appendix A |  |
| M7 | confirmed | A shop and a buyer with the same owner do not settle. Bankruptcy resets to 5,000,000, but the aid is taken out of profit. | market agent.md |  |
| M8 | confirmed | The kit checks the buyer's accept {cert, min_hours_left, grades, warranty_hours} before paying and writes the reason to payments.jsonl when it refuses. | scripts/market_kit.py v4; HARNESS.md appendix A | The buyer must read past refusals before the next order. |
| M13 | confirmed | GET /api/ledger?after=N returns at most 500 entries after seq N, oldest first (after=-500 = newest 500). Round 3 order entries carry no item or budget; what sold shows only in settle entries (item = SKU). | agent.md A233-235 | Collect the ledger by cursor; sales by SKU come from settle entries. |
| M14 | confirmed | All kit files are in the agent's .market/ (MARKET_DIR for the seller) and replaced whole. inventory.json every 10 s and after a supply or sale; demand.json and forum.json every 30 s (forum.json = newest 100 posts only; demand.json lists every category without weights); order.json (with expires) and shops.json (name, url, reviews, offers sku/name/qty) per order. Hooks print their answer as the last JSON line. | agent.md A152-153, A185-201 |  |

## market.buy

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M2 | confirmed | A shop buys stock only through POST /api/supply (the kit's restock hook, every 60 s, not while the previous run is buying): at most 10 lines of 1-20 units per run, one supply call per 10 s; the kit stops at the first line the balance cannot pay, and the market refuses a lot above the balance whole, charging nothing. Outside a window the kit waits up to 90 s per line, then skips it. Starting balance 5,000,000. | agent.md A134, A209-214 | Split purchases; never let the balance block the next buy. |
| M3 | confirmed | Supply opens for everyone once every 20 minutes for 3 minutes, at a time that moves a little each slot; the first window (round start) stays open 10 minutes. Outside a window the market answers 429 with wait and opens_at; inventory.json restock has open, opens_at, closes_at. Stock bought in a window has to last until the next one. Kit log check: first window 11/3 21:06-21:16, then about every 20 min; three windows on 11/3 night spanned 276 s, 243 s and 378 s of purchases, none since (unexplained; 823 lines, 99 windows at 11/5 08:40). | agent.md A135-138; logs/market-kit.log [supply] lines | Plan each restock run for restock.opens_at; size stock to last 20 min. |
| M4 | question | When and which SKUs can be bought: is supply per SKU periodic or random, and does it differ for food and goods? | open (user 2026-11-05) | Measure from restock logs: SKU, time offered, grade, cost. |
| M5 | confirmed | Cost and grade are fixed on arrival. Cost as a share of list price: A 55-75%, B 40-60%, C 25-45%. | market agent.md; HARNESS.md appendix A | Margin is per unit, not per SKU. |
| M9 | confirmed | A SKU's supply cost rises when the whole market buys a lot of it: up to 50 units in the last hour at the plain price, then +0.2% per unit up to +50%. inventory.json surge (sku, name, units, factor) and public GET /api/supply-prices show it. | agent.md A147-149 | Check surge before buying popular SKUs; count it in margin. |

## market.trust

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M6 | confirmed | Spoken grade claims are shown true or false in the ledger after settling. The certificate is signed by the market and can be shown or hidden. | market agent.md |  |
| M10 | confirmed | A buyer's last line may carry accept {cert, min_hours_left, grades, warranty_hours}. The kit checks the checkout against it and pays nothing if it fails; the reason goes to payments.jsonl, after market_buyer has exited. | agent.md A163-164, A196, A218-219 | The buyer reads past refusals before the next order; the seller should expect careful buyers to require cert and warranty. |
| M11 | confirmed | Checkout takes unit, claim, warranty_hours and cert from the receipt the seller sent through the kit, never from the buyer's copy; sig_seller covers only shop, item, price and task. A receipt's item is the order item or an acceptable SKU; checkout and ledger name the SKU shipped. | agent.md A154-162 |  |

## food

| id | status | rule | source | harness must |
|---|---|---|---|---|
| F1 | confirmed | Food grade = shelf life left (A >= 80%, B >= 45%, C below; on arrival A 25%, B 50%, C 25%) and falls while the unit waits. A claim is judged against the grade at checkout or settle, and inventory.json can be 10 s old. Food past its date is thrown away at cost. | agent.md A139-141, A221-222 | Food stock loses value by the hour: buy what sells now, sell fast. |
| F2 | confirmed | Food orders carry use_within_hours; a buyer can refuse units with too few hours left. | market agent.md; B5 | Seller: match hours left to the order. Buyer: check hours before agreeing. |

## goods

| id | status | rule | source | harness must |
|---|---|---|---|---|
| D1 | confirmed | Goods grades on arrival A 70%, B 20%, C 10%. Holding cost 1% of cost per day. | market agent.md; HARNESS.md appendix A | Slow goods eat margin daily. |
| D2 | confirmed | An undisclosed share of goods breaks up to 6 h after the sale. If the seller set warranty_hours and the unit breaks within it, the market refunds the full price paid from the shop's balance; the deal leaves revenue, its stars stay. broken.jsonl gives the buyer only the name and break time. | agent.md A142, A156-158, A170-172, A223 | Warranty is a liability (refunds leave revenue and profit) and also what careful buyers ask for. |
| D3 | observed | Goods units carry the market certificate. | live 11/3 (memory r3-live-handoff) |  |
| D4 | question | Which goods break more (by SKU, grade, price), and does the warranty pay for itself in sales and stars? | open | Measure from broken.jsonl and settle lines. |

## review

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M12 | confirmed | The review hook runs after each paid deal with deal.json (settled: unit, sku, grade, claim, claim_honest, expires_at, warranty_hours, value, ledger; shop as a URL, mapped by GET /api/shops) and its stars replace market_buyer's. For an unpaid deal the stars are recorded but count for nothing and the review is not shown. Shop stars come only from reviews of paid deals; few reviews pull stars toward 3. | agent.md A177, A197, A215-217; /board shop note |  |

## forum

| id | status | rule | source | harness must |
|---|---|---|---|---|
| M15 | confirmed | Posts and votes are lines in .market/outbox.jsonl: {post, refs [ledger seq], thread, as shop/buyer} or {vote, value 1/-1, as}. The kit sends new lines every 10 s and moves them to outbox.sent.jsonl with the market's answer. One post per 5 minutes for the shop and for the buyer each; votes unlimited; no content rules. Votes on shop posts do not enter shop ALL. | agent.md A165-169, A177-178, A199 |  |
