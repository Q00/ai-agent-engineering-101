# Ideas, 2026-10-21 14:26:21 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Mention-us returning-buyer discount like 11im (small code)  **FLAGGED: sells below the reserve**
- What: Add a true, signed follow-up incentive in the deal message: 'mention receipt No. X on your next visit for an extra 3% off', honored by code through the existing one-time loyalty code (never below reserve + 5). Use a flat 3% on the second visit, separate from the 1-6 tier table, and apply it only when the resulting price stays above the floor.
- Evidence: 11im (4801 deals, 20.8% share) ends every deal with 'Receipt No. ...: mention it on your next visit for an extra 3% off', while our share is 1.9% on 703 visits and our reviews show buyers returning at 105 on repeat rounds.
- Expected: Higher repeat visits and conversion per visit (445 deals / 703 visits is 63%, vs 11im 78%); modest share gain, small price give-up of about 3% on returns only.
- Risk: Margin loss on repeat buyers who would have paid full price anyway; overlap with tier discounts could stack below floor if the code doesn't cap it.
- Honest: The discount is real, honored by code, and stated exactly; no review is requested in exchange or conditioned on it, and the floor stays at reserve + 5.
- Test: A/B by receipt parity for 2 days: half get the 3% line. Compare return-visit rate, deals per visit and average margin.

## 2. [shop] Match the going lamp/chair/bookshelf price exactly on first counter (knob)
- What: Set opening ask and counter for each item to the middle-half lower bound (lamp 100, chair 180, bookshelf 260) after the first offer, rather than conceding 40% of the gap in steps. Keep a slightly higher opening ask only on bookshelf where the quartile range is 260-270, and quote one fixed 'listed price' that we really charge.
- Evidence: Going prices: lamp 100, chair 180, bookshelf 260. Top shops 11im, lee-sungsoo, dragonash, ggari-shop and nocha12 all counter at exactly 100/180/260, and corner-store, which sits above (chair 187, lamp 104), has only 6.1% share. Our reviews show lamp deals at 110 and bookshelf at 275, above the median.
- Expected: Fewer walk-aways and fewer multi-round negotiations (reviews note 2-3 rounds); likely a bump in share with about 5% lower average price on bookshelf and lamp.
- Risk: Gives up margin on buyers who would have paid 105-110; collapses to price parity with no differentiation.
- Honest: Prices are real, no fake 'was' price or scarcity claims; reserve + 5 floor is untouched.
- Test: Change meta_shop.py targets for 24 hours; compare deals per visit, average price paid and share against the previous 24 hours.

## 3. [shop] Short, fixed-price replies to cut buyer-side failures and latency (small code)
- What: Have code, not the Llama 8B, produce the counteroffer and deal text from templates ('X is too low. Counteroffer: N.' / 'Deal: lamp at N. Receipt No. ...'), and call the model only for an optional one-line description. Return quickly and avoid heavy model calls per turn.
- Evidence: Winners dragonash, ggari-shop, 11im and JangSeongWoong use terse templated replies ('89 is too low. Counteroffer: 100.'; 'Deal: lamp at 100.'). Our reviews include two 1* 'Failed to trade' entries after harness failures, and 4* reviews that mention speed ('quick deal (1s)').
- Expected: Fewer failed trades and 1* reviews, a slightly higher star average (4.42 toward 4.48) and a higher conversion rate.
- Risk: Plainer text may lose some of the warm tone that buyers' review prompts appreciate; template bugs could mis-state a price.
- Honest: Every number in the reply is computed by the same pricing code, so the message always matches the receipt.
- Test: Compare the rate of error/1* reviews and the mean response time for 500 visits before and after the change.

## 4. [buyer] Avoid shops with repeated walk-aways and cap budgets by item median (small code)
- What: Add a short per-shop memory: after one walk against a shop for an item at a budget below its floor, skip that shop for that item and budget band for a few hours, and go to a shop whose observed median is at or below budget (e.g. 11im/lee-sungsoo for chair only when budget is at least 180). When budget is under the going median, go straight to the cheapest shop with a lower-than-median report, such as 25620027 for lamp at 100.
- Evidence: Our buyer's 7 non-deals: lee-sungsoo chair budget 160 walked three times, 11im lamp budget 90 walked, 25620027 bookshelf walked/error. Going prices (chair 180, lamp 100) are above those budgets, and the buyer paid 84.1% of budget on average.
- Expected: Fewer wasted trades (7 of 40 failed), more deals per trade; no change in the price paid.
- Risk: Some budgets are simply below market, so no shop will sell; a skip list can wrongly exclude a shop whose prices dropped later.
- Honest: Uses only our own observed outcomes and public prices; no deception or pressure on shops, and the buyer never offers above budget.
- Test: Run the buyer for the next 40 trades with the skip rule and compare the no-deal rate with the 7/40 baseline.
