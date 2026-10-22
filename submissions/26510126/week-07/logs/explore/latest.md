# Ideas, 2026-10-22 19:37:36 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Answer a lowball with one exact-price instant-receipt line (small code)
- What: When an offer is below target, reply once with the final price and an exact-reply instruction, e.g. 'Our final price: 100. Reply 100 and the signed receipt comes back at once.' Skip the 40% concession ladder and the twice-shown floor on items already at the going-price median. Keep the reserve+5 floor in code.
- Evidence: ggari-shop (5.8% share, 4.41 stars): '97 is below the market floor for a lamp... Reply ok and the signed receipt comes back at once. Our final price: 100.' mjforge: 'reply with 180 alone for instant signed receipt.' 25620027: 'Reply with exactly 262 to complete the purchase.' Our own reviews show 'Actual offers: 2' on every deal, so we spend extra turns where rivals close in one.
- Expected: Fewer walked buyers and fewer 'No validated purchase' 3-star reviews. Roughly 5-10% more closed deals per visit. Price per deal stays flat or slightly up, since the 40% concession is skipped.
- Risk: Buyers who would have haggled lower now walk. Items priced above the median lose the haggle appeal. Lowballers may walk instead of conceding.
- Honest: The quoted price is the real price, reserve is never breached, and no false scarcity or deadline is claimed.
- Test: A/B by visit parity for 3 hours. Compare deals/visits, average price over reserve, and the share of reviews that say 'No deal'.

## 2. [shop] Put the exact deal facts in the thank-you and review request (small code)
- What: Rewrite the post-deal message as one factual line with item, agreed price, receipt confirmation and the market median, e.g. 'Deal: chair at 180, exact price on a signed receipt, going price 180.' Then add the existing honest-review request unchanged. Do not suggest a rating or tie anything to it.
- Evidence: Our reviews are 3* and read as dry logs ('Purchase completed at 265; budget was 270... Actual offers: 2'). Rival 5* reviews quote concrete facts: fair-furniture 'ask fell from 273 to 265 to 260, I paid 260... signed by the shop'; ggari-shop's review scores price, speed and receipt, citing the median. Our stars are 4.53 against fair-furniture 4.61 and 11im 4.58.
- Expected: Buyers score price and receipt from the facts we state, which lifts the stars by about 0.05-0.1. Higher stars should lift selection by review-reading buyers over time.
- Risk: Buyer reviewers may ignore the text. Quoting the median could expose a price above it, which would draw a lower score.
- Honest: Only true, verifiable facts go in. The message does not ask for stars or condition anything on a review.
- Test: Compare mean stars and the share of 3-star reviews before and after over about 500 deals.

## 3. [shop] Hold bookshelf and chair at the cluster price and drop the opening ask (knob)
- What: Set the opening ask for each item to the middle-half low end (bookshelf 260, chair 180, lamp 100) and accept at or above it. Concede only if the code can stay at or above reserve+5. This replaces the current quartile-derived ask, which may sit near the top of the band.
- Evidence: Going prices: middle half bookshelf 260-265, chair 180-181, lamp 100-105. The top shops (lee-sungsoo, dragonash, mjforge, JangSeongWoong) all have medians of exactly 260/180/100. Shops that ask above the band (yms979 at 188 chair, 272 bookshelf, 4.23 stars, 4.1%; 11im chair 195) have lower share, and our own deals sit at 265-268.
- Expected: Share rises toward the 11-13% of the leaders, since buyers skip shops above the upper quartile. Margin per deal falls by about 3-8 credits.
- Risk: Margin shrinks if reserve is close to the band. Volume may not respond if buyers rank on reviews rather than price.
- Honest: Real lower prices, no false 'was' price, and the reserve floor is kept.
- Test: Run for 2 hours against the control ask. Compare share, deals/visits and total margin (deals x (price - reserve)).

## 4. [buyer] Make a first offer of the going-price floor and accept a close counter at once (small code)
- What: When a shop has historically sold near the median, open at the middle-half low end (bookshelf 260, chair 180, lamp 100). If the counter is within the budget and at or below the upper quartile, accept immediately rather than make the three-offer walk. Walk only when the counter exceeds both the budget and the cap.
- Evidence: 9 of the last 40 trades ended without a deal, and the walks are against the very shops that sell at the median: fair-furniture (budget 160 for a chair, 90 for a lamp, 230 for a bookshelf), lee-sungsoo bookshelf 230, dragonash chair 160. The going prices are 180 chair, 100 lamp and 260 bookshelf, so those budgets were below every shop's price. The deals paid 85.1% of budget, which leaves room.
- Expected: Fewer wasted visits and a higher deal rate on orders whose budget reaches the median. For orders with a budget below the median, a walk right away saves the three-offer exchange. Price paid stays close to the median.
- Risk: Skipping the haggle can mean paying 2-5 above what a patient counter would reach. Early acceptance on cheap budgets could cost a few credits.
- Honest: The buyer never goes above budget, and its reviews stay factual. It does not misstate its budget or invent other offers.
- Test: Compare deal rate and the price paid relative to the median over 40-60 trades against the current policy.
