# 시장학개론 (Market primer for the Round 3 agents)

Each section is one principle: the idea with its source, what it means in this market, where our code applies it,
and what to look at before acting on it. The `primer` tool (r3lib/primer.py) returns the sections that best match
a query, so a ReAct agent reads two relevant principles instead of the whole file. Sources were gathered by web
search on 2026-11-03; several are secondary summaries and are marked as such.

## 1. Reservation price, BATNA and ZOPA
tags: negotiation, bargaining, reservation, walk-away, surplus, floor, max_pay, 흥정, 유보가격
Idea: each side has a walk-away point (reservation price, set by its best alternative, BATNA). A deal is possible
only when the buyer's maximum exceeds the seller's minimum (the zone of possible agreement); the price decides how
that surplus is split. Sources: https://en.wikipedia.org/wiki/Reservation_price ,
https://resources.rework.com/libraries/deal-closing/zopa (training blog).
Here: the shop's reservation is `unit_floor` (cost plus margin, lower near expiry or round end); the buyer's is
`buyer_limits` (list price minus required surplus, minus break risk). Value to an order is the list price.
Check: price/list ratios that settle in a category (memory.price_ratio) show where the two walk-away points meet.

## 2. First offers anchor the result
tags: anchoring, first offer, opening, ask, concession, 첫 제안, 앵커
Idea: the first number pulls the final price toward it; whoever opens high (seller) or low (buyer) tends to do
better, but extreme anchors raise impasses and slow deals. Sources: Galinsky & Mussweiler (2001) via
https://www.kellogg.northwestern.edu/faculty/research/detail/2001/first-offers-as-anchors-the-role-of-perspective-taking ,
backfire evidence https://www.cambridge.org/core/product/F38433715E3D9D74BF5ACFCE6869BE83/core-reader .
Here: the shop opens near the market's settled ratio plus a little (`unit_ask`), the buyer at FIRST_OFFER_RATIO of
list. A buyer opening at 25 credits on a 279,000 mattress is an anchor so extreme the shop simply ignores it.
Check: our close rate (seller_outcomes) — if anchors cause too many no-deals, the ask is too far from the ZOPA.

## 3. Newsvendor: how much perishable stock to hold
tags: inventory, restock, newsvendor, critical ratio, overstock, understock, perishable, 매입, 재고
Idea: for one selling period, order up to the demand quantile at the critical ratio Cu / (Cu + Co), where Cu is the
margin lost by being out of stock and Co the loss on an unsold unit. Sources:
https://metricgate.com/docs/newsvendor-model , lecture notes https://eclass-b.uoa.gr/modules/document/file.php/MATH177/%CE%A3%CE%B7%CE%BC%CE%B5%CE%B9%CF%8E%CF%83%CE%B5%CE%B9%CF%82%20-%20%CE%A5%CE%BB%CE%B9%CE%BA%CF%8C/Newsvendor%20model.pdf .
Here: `pricing.ev_quantity` buys the j-th unit while P(demand >= have + j) * margin > (1 - P) * loss, which is the
same quantile rule with Poisson demand. Food that expires before the next chance has Co = full cost, so the
ratio drops and fewer units are bought; cheap long-life food has small Co, so more.
Check: sell_through per category; a low sell-through means demand was overestimated or Co underestimated.

## 4. Expected value under uncertainty
tags: expected value, EV, risk, probability, decision, 기대값
Idea: compare options by probability-weighted payoff, and keep tail risk visible when one outcome is large
relative to the gain. Standard decision theory; used across the sections below.
Here: warranty only when p_break * price is under half the margin; the warranty floor adds p_break * price; the
buyer's limit subtracts the break risk. A 190k refund risk on a 10k margin is a bad bet even at p = 2%.
Check: memory.break_rate by grade (smoothed) against raw counts (forum_r3.raw_breaks).

## 5. Markdowns for perishables
tags: markdown, clearance, discount, expiry, perishable, food, round end, 할인, 유통기한
Idea: buyers prefer fresher stock at the same price, so near-expiry units need a discount; most of the benefit
comes from a single well-timed markdown, and more stages help when expiry costs are high. Sources:
https://ideas.repec.org/a/taf/tprsxx/v63y2025i15p5692-5722.html (Moshtagh, Zhou & Verma 2025),
https://edepot.wur.nl/707979 (Haijema et al.).
Here: `unit_floor` drops to 80% of cost under 12 h left and 30% under 6 h; in the last CLEARANCE_HOURS of the round
everything slides toward a salvage floor. Any price above zero beats throwing the unit away at cost.
Check: units in stock_on_hand with min_hours_to_expiry under 12 and no recent deal.

## 6. Lemons: hidden quality drives good sellers out
tags: asymmetric information, quality, lemons, adverse selection, grade, certificate, 정보 비대칭
Idea: when buyers cannot see quality they pay for average quality, so good sellers leave and quality falls
(Akerlof 1970). Certification, warranties, inspection and reputation restore trade. Sources:
https://saylordotorg.github.io/text_introduction-to-economic-analysis/s19-01-market-for-lemons.html ,
https://umbrex.com/resources/economics-concepts/microeconomic-theory/akerlof-lemons-model-adverse-selection/ .
Here: the market certificate proves grade and shelf life; our buyer requires it (accept.cert) and our shop
always ships it. Grade C goods keep full value unless they break, so the buyer accepts them at a price that
prices the break risk.
Check: payments.jsonl refusal reasons — "no certificate" refusals show which shops still sell blind.

## 7. Warranties as a costly signal
tags: warranty, signal, signalling, Spence, insurance, refund, 보증
Idea: a warranty signals quality because it is cheaper for sellers whose goods rarely fail (Spence; Akerlof). It
also insures the buyer. Caveat: buyers often do not compare warranties before buying, so the signal may be weak.
Sources: https://www.econlib.org/library/Enc/bios/Akerlof.html ,
https://pure.psu.edu/en/publications/a-reexamination-of-the-lemons-market-when-warranties-are-not-prep/ .
Here: goods get WARRANTY_HOURS when the expected refund is small relative to the margin; the floor carries it.
Check: whether buyers choose our shop more when the pitch mentions a warranty (negotiation outcomes by warranty).

## 8. Reputation in repeated play
tags: reputation, stars, reviews, repeated game, trust, honesty, claim, 별점, 평판
Idea: in repeated markets, reputation sustains honest behaviour; a first negative rating hurts a lot; raw scores
inflate and feedback is reciprocated or retaliated. Sources: Tadelis (2016) https://ideas.repec.org/a/anr/reveco/v8y2016p321-340.html ,
Resnick & Zeckhauser https://presnick.people.si.umich.edu/papers/ebayNBER/index.html .
Here: claim_honest is public on the ledger, so the shop always claims the true grade; stars are 0.2 of shop ALL.
Our reviews state checkable facts. Downvoting honest posts invites retaliation, so votes need high confidence.
Check: board stars and honest_claims; votes received on our posts.

## 9. Competition and market share
tags: competition, share, demand, rivals, entry, price war, 경쟁
Idea: when many sellers stock the same item, each one's share of demand shrinks and prices fall toward cost;
profit comes from categories where demand outruns supply. Standard industrial organisation.
Here: restock divides category demand by (1 + competitors holding stock); categories where the market pays under
65% of list are skipped (no margin over typical cost).
Check: category_evidence (competitor_shops_with_stock, price_to_list).

## 10. Concentration risk
tags: diversification, concentration, portfolio, cap, risk, 분산, 쏠림
Idea: putting most capital into one product makes profit depend on one demand estimate being right.
Here: CATEGORY_MAX_SHARE caps one category at 25% of the stock budget (35% for deep categories, see 15). On 2026-11-03 the old rule bought eight
mattresses and two fridges (about 2.2M) in one supply window before the cap existed.
Check: stock_on_hand cost by category.

## 11. When to speak first (mixed initiative)
tags: forum, post, proactive, initiative, Horvitz, interruption, noise, 게시판, 선제 발화
Idea: act unprompted only when P(the audience wants it) exceeds p* = C / (B + C) (Horvitz 1999, week-09 reading).
Here: initiative.py; B counts votes and the post-count score, C grows with dislikes and recent posts; the
probability comes from the decision model when configured.
Check: initiative.jsonl decisions and the likes/dislikes on our posts.

## 12. Information as a product
tags: information, report, forum, signal, data, facts, 정보
Idea: information others lack is valuable; repeated public information is not. Checkable claims (with sources)
earn trust; uncheckable ones are discounted.
Here: buyer posts lead with first-hand facts (our_experience) and cite ledger seqs; raw counts, not smoothed
estimates, go into public posts.
Check: whether a draft repeats recent_forum.

## 13. Supply signals and the bullwhip effect
tags: supply, competitors, pressure, bullwhip, overstock, gap, scout, 공급, 경쟁사 매입, 과잉재고
Idea: when each seller orders on its own forecast, orders swing harder than demand (the bullwhip effect), so
looking at what the whole chain is ordering, not only at what sold, avoids piling into a category everyone else is
piling into. Source: Lee, Padmanabhan & Whang (1997), "Information Distortion in a Supply Chain: The Bullwhip
Effect", Management Science 43(4), https://doi.org/10.1287/mnsc.43.4.546 .
Here: scout.py reads competitors' `supply` entries on the public ledger; pressure = units supplied / units settled
over 6 h. Pressure under 0.6 scales the demand target up to 1.3x, over 1.4 down to 0.7x. On 2026-11-04 the big
categories sat near 1.0 (침구 0.99, 매트리스 1.04) and 소파/의자 was crowded (1.32).
Check: the `scout` tool's gaps, and gap_factor in restock.jsonl.

## 14. Switching costs and repeat customers
tags: loyalty, member code, repeat, switching cost, retention, 단골, 회원 코드, 재방문
Idea: a buyer who has to give something up to switch (a discount, a known good record) comes back; sellers
compete hard for first purchases and then earn on the repeat ones. Source: Klemperer (1987), "Markets with
Consumer Switching Costs", The Quarterly Journal of Economics 102(2), https://doi.org/10.2307/1885068 .
Here: every receipt carries a member code; a buyer that quotes a valid code gets LOYALTY_DISCOUNT off the first
ask (the floor does not move). Buyers in this market each have a few favourite categories, so repeat orders are
likely. dragonash and chaewoonee ran similar codes on 2026-11-04.
Check: `member_code` and `loyalty` events in the shop log; whether quoting buyers close more often.

## 15. Returns on inventory investment
tags: capital, budget, GMROI, turnover, idle cash, stock budget, 자본, 재고 예산, 회전율
Idea: stock is capital at work; gross margin per credit of average inventory (GMROI) is what matters, and cash
that sits idle earns nothing while stock that does not sell loses its cost. Source:
https://en.wikipedia.org/wiki/Gross_margin_return_on_inventory_investment (encyclopedia summary).
Here: config.stock_budget lets stock cost grow to 40% of the balance (3M-8M), and deep categories (>= 100 market
settles/h) may take 35% of it. On 2026-11-04 a 15.8M balance held 1.6M of stock while the mattress EV said 20
units and the old 750k category cap allowed 3. Round-end risk stays with STOP_BUYING_HOURS and clearance.
Check: stock_budget and stock_cost in restock.jsonl; profit on the board after the change.

## 16. Price dispersion and search
tags: price dispersion, search, price check, oracle, median, information, 시세, 가격 분산, 탐색
Idea: when buyers know little about prices, the same good sells at very different prices; every observed price
lowers that dispersion, which is why price information is worth paying (or voting) for. Source: Stigler (1961),
"The Economics of Information", Journal of Political Economy 69(3), https://doi.org/10.1086/258464 .
Here: oracle.py gives low/q25/median/q75/high per SKU and category from settles; the report lane posts them with
ledger refs. On 2026-11-04 one mattress SKU (G1515d951) settled from 187,359 to 417,858 in three hours, so the
spread is real information for buyers.
Check: the `oracle` tool; likes on our report posts.
