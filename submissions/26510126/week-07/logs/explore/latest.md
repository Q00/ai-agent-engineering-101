# Ideas, 2026-10-27 17:42:01 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Structured factual deal summary in the closing message (small code)
- What: After each deal, have the code (not the 8B model) append a one-line factual summary: item, agreed price, number of offers, and that the signed receipt matches the agreed price. Keep the existing thank-you and request for an honest review, with no star or rating wording.
- Evidence: Our reviews are often generic ('Deal completed within budget and the receipt matched the final offer.') and some buyers score price 3/5. ggari-shop's 5* reviews cite specifics ('Fair price: chair for 180 after 2 offer(s). Receipt matched the agreed price.') and its stars are 4.55 vs our 4.5. 25620027 and psyoung82 close with 'Seller-signed receipt matches the agreed price'.
- Expected: Reviews that state price and receipt facts more consistently; small stars gain (about +0.03 to +0.05) and a little more buyer trust, so a slow rise in share from 2.6%.
- Risk: Buyer review styles are set by the buyer agent, so the effect may be nil. It also adds a few tokens per message.
- Honest: Every statement is true and generated from the real deal record. It only makes the facts easier to see and asks for an honest review, without conditioning on stars.
- Test: Run A/B by visit-code parity for 24h. Compare mean stars, share of reviews that mention price and receipt, and deal rate per visit.

## 2. [shop] Lamp and chair asking-price tier matched to the top-3 cluster (knob)  **FLAGGED: sells below the reserve**
- What: Check meta_shop.py's opening ask and target for lamp and chair against the cluster median. Lamp 100 and chair 180 are where ggari-shop, dragonash, lee-sungsoo and cheapest-in-town all sell. Our own lamp deals close at 102-105 and show 'price 3/5 (median 103)'. Lower the lamp and chair targets by 2-3 credits, never below reserve + 5, and keep the bookshelf unchanged.
- Evidence: Going prices: lamp middle half 100-105, chair 180-185, bookshelf 260-270. Our review: 'lamp at 105 | price 3/5 (median 103)'. Top shops by share all have medians of exactly 100 / 180 / 260.
- Expected: Price score goes from 3/5 to 4/5 on lamps. Deal rate and share go up, since the buyer skips shops above the upper quartile. Revenue per deal drops 2-3%.
- Risk: Margin loss that volume may not repay. Competitors may cut too. Check that reserve + 5 still holds.
- Honest: Only moves real prices toward the market level, no claims about prices. Reserve and budget rules are untouched.
- Test: Change the knob for lamp for 6 hours and compare to the previous 6 hours on deals per visit, average price, stars and margin per visit.

## 3. [buyer] Stop revisiting shops that walked us on the same item and budget (small code)
- What: In buyer memory, key walks by (shop, item, budget band). After one walk, skip that shop for that item and band for a fixed time window (for example 3 hours) and pick the next best-reviewed shop. Retry only when the going price drops or the budget changes.
- Evidence: Of the last 10 no-deals, fair-furniture appears 4 times (bookshelf budget 230 twice, lamp budget 90 twice, one of them an error), and lee-sungsoo lamp 90 also walked. Going prices are bookshelf 260 and lamp 100, so budgets of 230 and 90 are below every ask. Last 40 trades: 30 deals, 10 failures.
- Expected: Fewer wasted trades and a deal rate above 75%. Where the budget is below the market the walk is still recorded, but no extra visits are spent on it.
- Risk: A shop may cut prices later and we would miss it. The window and the reset rule limit that.
- Honest: It only changes which shops we pick from our own records. It does not attack anyone or ask for reviews.
- Test: Count repeat walks per (shop, item, budget) before and after one day, and the deal rate over the last 40 trades.

## 4. [buyer] Prefer shops whose signed receipts and review text carry facts, with a stronger weight on offers-to-deal (small code)
- What: Add the visible 'after N offers' pattern and the shop's typical share of budget paid to buyer scoring as a tie-breaker among shops at equal price. Favor shops where reviews report 2-3 offers and a deal at or near the median (ggari-shop, fair-furniture) over ones where reviews report 4+ offers or 'held firm' at the ceiling.
- Evidence: ggari-shop reviews: 'after 2 offer(s)', '88% of budget'. cheapest-in-town: '4 offers until the end'. fair-furniture 3*: 'got me to within a dollar of my ceiling'. Our buyer pays 83.9% of budget on average.
- Expected: Slightly lower average price paid and fewer failed negotiations. Payment as a share of budget drops by about 1-2 points.
- Risk: Reviews are written by other agents and can be noisy. Small samples bias the score, so cap the weight.
- Honest: The buyer reads public reviews and its own memory, and keeps the rules (pays no more than 1.1 x the upper quartile, within budget, factual reviews).
- Test: Run the new tie-breaker on half of the buyer tasks. Compare average share of budget paid, offers per deal and the failure rate.
