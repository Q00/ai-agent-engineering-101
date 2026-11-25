# Brief 2026-11-03T19  (strategy v3, open)

## 1. Score
No board entry for us yet. eval.score still shows Round 2 data, with shop, buyer and player all null and no percentiles. Market counts: 77 shops, 115 buyers. Base condition: shop 0/20, buyer 0/20.

## 2. What happened
- Our shop and buyer have had no Round 3 activity. The shop has no stock listed, so no deals were possible.  _(evidence: eval.funnel seller: negotiations 0, closed 0, completed 0, settled 0, eval.funnel buyer: orders 0, picked 0, agreed 0, paid 0, me.inventory: units 0, stock_value 0, balance null)_
- In the last 6h, all 684 settled market deals were on three furniture SKUs: lamp, chair and bookshelf. No food or other goods deals were seen, and no categories are listed.  _(evidence: market.selling 6h: lamp 244, chair 231, bookshelf 209, market_categories empty)_
- Volume sits with a few shops. shaun0927 has 142 deals at 4.5 stars, lee-sungsoo 112 at 4.2, Sungjin Shop 75 at 4.66, and the new entrant kimauve 55 at 4.2.  _(evidence: me.counterpart shaun0927, lee-sungsoo, Sungjin Shop, kimauve, participants new_6h: 11im, cheapest-in-town, dae-yangkim, kimauve, 김관우)_

## 3. The hypothesis in force
H1 cannot be judged yet. The sample is 0 of 30 closed negotiations. No losses so far (rollback threshold 500,000 not approached).

## 4. Counterparts
No deals with anyone yet, so no stance changes. Top shops (shaun0927, Sungjin Shop, lee-sungsoo, kimauve) are buying candidates for our buyer and rivals for our shop. Our buyer should explore them and check receipts and certificates. All 5 new participants are first-timers to explore. There is no evidence of dishonesty for anyone yet.

## 5. Options
### A
- change: `{"context": ["Round 3 so far: all 684 settled deals in the last 6h were lamp (244), chair (231) and bookshelf (209). No food or other goods deals have been seen yet.", "Top shops by volume: shaun0927 142 deals at 4.5 stars, lee-sungsoo 112 at 4.2, Sungjin Shop 75 at 4.66, kimauve (new) 55 at 4.2.", "We have no stock and no deals yet. Base condition 0/20 for shop and buyer."]}`
- targets: all parts, indirectly: executors decide with current facts
- expected: Executors see where demand is. Priorities stay broad across many categories as in v3.
- risk: Broad small stock may sit unsold while demand is only on furniture. That means storage cost and slow exposure.
- can be undone: yes

### B  (recommended)
- change: `{"context": ["Round 3 so far: all 684 settled deals in the last 6h were lamp (244), chair (231) and bookshelf (209). No food or other goods deals have been seen yet.", "Top shops by volume: shaun0927 142 deals at 4.5 stars, lee-sungsoo 112 at 4.2, Sungjin Shop 75 at 4.66, kimauve (new) 55 at 4.2.", "We have no stock and no deals yet. Base condition 0/20 for shop and buyer."], "priorities": ["Get stock listed now. First stock lamp, chair and bookshelf (up to the 5-units-per-SKU limit), with easy first prices just under what the market settles. Below cost only within the 0.1 limit, and only for a first deal with a new buyer.", "Keep a small probe of 1-2 units in a few other goods categories to learn whether other demand appears. Hold food to a minimum until food deals are seen in market.selling.", "Be reliably honest: true grade claims, receipts that match, the market's certificate shown.", "Buyer: start placing deals toward 20 settled. Read market prices, check certificates, shelf life and warranty. Try top shops and new participants.", "Record everything. Revisit after the first 30 closed negotiations."]}`
- targets: shop revenue and stars via exposure; base condition (20 deals each); buyer deal count
- expected: First deals within the next slice, on the only SKUs with observed demand. Faster progress to 20 settled deals.
- risk: Head-to-head competition with established furniture shops on price may push margins to the floor (as in Rounds 0-2). Over-concentration if demand shifts. Capped by 5 units per SKU and 0.4 stock-value share.
- can be undone: yes

## 6. Recommendation
B

Answer: `py -m outer.decide 2026-11-03T19 <option id|reject>`

## Audit (another vendor's model)
agrees: True
- Option B's proposal to prioritize stocking lamp, chair, and bookshelf with easy first prices aligns with the 'exposure first' priority and the goal of reaching the base condition.: The market.selling data clearly shows all recent settled deals are on these three furniture SKUs. To achieve 'exposure first' and 'be seen by as many different agents as possible' (user intent point 1, strategy priorities), stocking what is currently in demand is the most direct path. This will also facilitate reaching the 'base condition early: 20 settled deals each for the shop and the buyer' (current priorities). The 'easy first prices just under what the market settles' strategy directly addresses the observation that our week-07 shop failed due to 'first price was too high', and is explicitly allowed by 'below cost: allowed in phase exposure, losing at most 0.1 of cost per deal' in risk limits. The risk of 'head-to-head competition... may push margins to the floor' is acknowledged and mitigated by the 'capped by 5 units per SKU and 0.4 stock-value share' risk limits.
- Option B's recommendation to hold food to a minimum until food deals are seen is a prudent response to the current lack of food demand and aligns with the 'learn' priority.: The 'happened' section explicitly states 'No food or other goods deals were seen' and 'market_categories empty'. Stocking food would contradict the 'Exposure first' priority (as no demand is observed) and would incur 'storage cost' (a shop loss per scoring formula). The strategy to 'Keep a small probe of 1-2 units in a few other goods categories to learn' also aligns with the 'Learn: explore participants we have not traded with, and record everything' priority, ensuring we are not completely blind to shifts in demand.
