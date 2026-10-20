# Ideas, 2026-10-20 23:20:58 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Anchor opening ask at the market median, not above (knob)
- What: In meta_shop.py set opening ask to roughly the going median plus a small margin (bookshelf ~270, chair ~187, lamp ~104) and target at or just below the median (bookshelf 262, chair 181, lamp 100), instead of the current higher opening and target from quartiles. Keep reserve+5 floor in code.
- Evidence: Our reviews show 'Paid 260 of 325', 'Paid 260 of 295', 'Paid 260 of 270', 'Paid 185 of 185' and 3* ratings; top shop 11im sells at 100/263 with medians 100/261/180, wins 23% share on 670 deals, and a 3* review on 11im notes 'opened at 290' yet still passed. JangSeongWoong got a 3* for an opening ask padded above the market average. Our 51 deals out of 71 visits is only 1.8% share.
- Expected: Higher conversion and stars on repeat shoppers who compare asks; share up from 1.8% as buyers who skip shops above the upper quartile stop skipping us. Slightly lower average price per deal, offset by volume.
- Risk: Lower margin per deal; if reserves are close to median there is little room. Median-anchoring could leave money on the table with high-budget buyers.
- Honest: Prices are real and honestly stated; no fake 'was' price. Floor stays at reserve+5 and we never exceed the buyer's budget.
- Test: A/B by 15-minute meta_shop cycles or by item: compare deals/visits, share, mean stars and mean price vs. the previous 51-deal baseline over the next ~100 visits.

## 2. [shop] Accept-fast on offers near median with a one-step counter (knob)
- What: Lower the auto-accept threshold to the going median minus ~2% for each item, and when below that, counter once at the median rather than conceding 40% over several rounds. Reduce wasted rounds that end in a walk or reject.
- Evidence: Our reviews include 2* 'Could not agree on a price' and 2* 'No completed purchase of bookshelf within budget 230 (walked)'. Top shop dragonash counters flatly ('254 is too low. Counteroffer: 260.'), and ggari-shop counters once then closes ('Deal: chair at 185'). 11im's deal came 'after 2 offers'.
- Expected: More completed deals per visit (our 51/71 = 72% vs 11im 82%, dragonash 83%, ggari 83%), fewer 2* reviews, modest rise in stars.
- Risk: May accept slightly lower prices than a longer negotiation would yield; buyers could learn to lowball at the threshold.
- Honest: Terms stated plainly in the counter; no pressure, deadlines or invented competition; reserve+5 floor remains.
- Test: Track deal rate per visit, number of offers per deal, and the share of 2* 'no agreement' reviews before and after for a fixed number of visits.

## 3. [shop] Make the receipt text and loyalty message review-friendly through verifiable facts (small code)  **FLAGGED: ties a reward to reviews or stars**
- What: Have the deal message state the facts a buyer's review can cite: item, price, 'signed receipt attached, verifiable with the market kit', and the loyalty code with its discount, in one short line like 11im's. Keep the request for an honest review of whatever they experienced; do not ask for stars or tie anything to a review.
- Evidence: 11im's messages are one line ('Deal: lamp at 100. Receipt No. ...: mention it on your next visit for an extra 3% off') and its reviews say 'Receipt verified locally; fair price within budget' and 'signed receipt matched the deal'. Our reviews are mostly bare 'Paid 260 of 295.' with 3* and no mention of the receipt or fairness.
- Expected: Reviews that mention verified receipts and fair prices are what buyers and the ledger weigh; may lift stars from 4.47 toward 4.6 and attract the buyers who skip unsigned or unclear shops.
- Risk: Marginal effect if buyers do not read message text; a short loyalty discount like 3% costs a little margin.
- Honest: Only states true facts (a real signed receipt, a real discount). The review request is neutral and no reward is linked to a review.
- Test: Compare the fraction of reviews that mention receipt/fairness and mean stars over the next 50 deals vs. the previous 51; check repeat-visit rate via loyalty code redemptions.

## 4. [buyer] Cap offers at the going median and open at the median-low end (knob)
- What: Change the buyer's ceiling from 1.1 x upper quartile to about the going median + 3% (lamp ~107, chair ~190, bookshelf ~275), open near the lower end of the middle half (lamp 100, chair 180, bookshelf 260), and prefer shops whose median is at or below that. Do not raise past the cap even if the budget allows.
- Evidence: Our buyer paid 107 for a lamp and 192 for a chair at corner-store while 'it sold at 100 / 180 to others'. Average paid is 84% of budget, and shops like 11im (lamp 100, chair 180, bookshelf 261), JangSeongWoong (180/260/100) and lee-sungsoo sell at the median.
- Expected: Lower average price paid by ~3-6% on affected trades with little loss in deal rate, since several shops sell at median.
- Risk: More walks (7 of 40 trades already had no deal) if a shop won't go to median; could slightly reduce the number of deals.
- Honest: Only reduces what we pay and uses public market data; no false claims to sellers, and never offers beyond budget.
- Test: Compare mean price paid vs. going median per item and the deal rate over the next 40 trades, against the last 40 (33 deals at 84% of budget).
