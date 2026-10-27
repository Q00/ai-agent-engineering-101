# Ideas, 2026-10-27 18:42:40 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Price bookshelf at the market median instead of 265-275 (knob)
- What: In meta_shop.py, set the bookshelf opening ask to 262-265 and target to 260 (the going median, middle half 260-270). Keep reserve+5 floor. Leave lamp and chair as they are. Optionally accept any bookshelf offer >= 260 immediately on the first offer.
- Evidence: Our own 2* review: 'seller's asking prices (265-275) far exceeded my budget and maximum offers (200-229)'. Several of our bookshelf reviews show deals at 275 and 265 while the going median is 260 and top shops (ggari-shop, dragonash, mjforge) all sit at 260 with 'Reply with 260 alone for instant signed receipt'.
- Expected: Fewer walk-aways on bookshelf, higher deal conversion and visits-to-deals ratio (ours is 8908/11513, 77%, versus ggari-shop 76%, dragonash 75%). Slightly lower average bookshelf price but more volume and fewer 2-3* reviews, helping stars and share.
- Risk: Lower margin per bookshelf; if the reserve is near 255 the margin shrinks to almost nothing. Buyers anchored at 260 may still not buy if budgets are lower.
- Honest: Only changes our own real asking price to match the real market median. No false claims, never below reserve+5.
- Test: A/B by hour or by visit parity for 24h: bookshelf ask 265-275 versus 260-265. Compare conversion, average price, and review stars on bookshelf deals.

## 2. [shop] Instant-accept quote: one number, one signed receipt (small code)
- What: For lamps, chairs and bookshelves, make the first reply state our true current best price in the style 'Price: X. Reply with X for an instant signed receipt' when X is at or below the market median, and skip the counter-offer loop if the buyer offers >= X. Truthfully say it is our lowest price only if it is the floor.
- Evidence: mjforge ('promo price: 260. Listed asking price: 260. Reply with 260 alone for instant signed receipt', 7.9% share, 26714 deals) and cheapest-in-town ('Deal: chair at 180. Zero added fees; your signed receipt records this exact item, price and task') convert at roughly 80% (26714/33553 and 27857/36394 visits) while using a short, clear deal flow.
- Expected: Higher conversion and speed score in buyer reviews (mjforge reviews explicitly score 'speed 5/5 (2 offers)'), fewer 2-3* reviews about negotiation friction, modest share gain.
- Risk: Giving up the negotiation margin on buyers who would have paid more; the 8B model might phrase the quote inconsistently with the code price.
- Honest: The quoted price is the one we really charge and the receipt records exactly it; no invented scarcity or deadlines.
- Test: Template the reply in code (not the LLM) for a share of traffic; compare deals per visit, mean sale price and mean stars versus control for 24h.

## 3. [buyer] Don't walk on budgets below median; try the shops that actually sell near budget (small code)
- What: When the buyer's budget is below the going median (e.g. lamp budget 90, chair budget 160, bookshelf 230), rank shops by observed lowest-accepted price from our own memory and the public ledger and try the top 2-3 in sequence, rather than a single walk-away at lee-sungsoo or mjforge. Record per shop 'min accepted price relative to median' and update after every trade.
- Evidence: 10 of the last 40 trades ended without a deal, and the listed ones are all low-budget walks (lee-sungsoo chair 160 x2, lee-sungsoo lamp 90, mjforge lamp 90, inextro chair 160, jiseokkk bookshelf 230) against medians of chair 180, lamp 100, bookshelf 260. Shops like yms979 and 11im sometimes quote at floors (lamp 100/105) and others accept 'desired price or lower, subject to minimum', so a price discovery step may find a cheaper floor.
- Expected: Recovers some of the 25% no-deal rate; probably not all because budgets 10-15% below median may be below every reserve. Cost is a few extra offers, not extra money.
- Risk: Wasted offers on shops whose floor is above budget; more traffic for those shops and slightly slower trades.
- Honest: Buyer never exceeds its budget and uses real offers; no deception.
- Test: Compare deal rate for budget < median-5% before and after over the next 40-80 trades; also track average paid percent of budget.

## 4. [shop] Keep the loyalty code but print a clear receipt summary in the thank-you (knob)
- What: In the post-deal message add a one-line, factual summary buyers can quote in reviews: 'Deal: <item> at <price>, budget respected, signed receipt matches, N offers.' Keep the honest-review request neutral and do not ask for stars.
- Evidence: Top-rated reviews quote exactly this structure: fair-furniture 'Deal at 260 after 1 offer(s); signed receipt matched the agreed price' (4.58 stars), mjforge 'price 4/5 | speed 5/5 (2 offers) | receipt 5/5 (matched, signed)'. Our own reviews are 4-5* but vary and include a 2* with no factual anchor. psyoung82 and 25620027 use similar 'signed receipt matches the agreed price' wording.
- Expected: More consistent 4-5* factual reviews and slightly higher star average (ours 4.5 versus 4.57-4.58 for the leaders), which feeds buyer selection.
- Risk: Reads as templated; if buyers copy it blindly it could look manufactured, and it adds a few tokens to every message.
- Honest: It states only verifiable facts about the deal and asks for an honest review without conditioning or scripting stars; reviews remain the buyer's own.
- Test: Compare mean stars and share of 5* reviews for 48h with and without the summary line using alternating sessions.
