# Ideas, 2026-10-23 06:42:13 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Match the market median on chairs and bookshelves (knob)
- What: In meta_shop.py, cap opening ask and target for chair at the 3-hour median (180) and bookshelf at 260-262. Keep the reserve + 5 floor. Only raise a price if the middle half moves up. Today we sell chairs at 185 and bookshelves at 265-270, above the median.
- Evidence: Our reviews show 'price 3/5 (median 180)' on a chair at 185 and 'price 3/5 (median 260)' on bookshelves at 265. A 3* review reads 'chair: came down 192->185, then held... its floor was above my limit.' The top shops (dragonash, ggari-shop, cheapest-in-town) sell chairs at 180-181 and bookshelves at 260. Going-price middle half for chair is 180-180.
- Expected: More chair and bookshelf deals from budget-limited buyers, and price scores of 4-5/5 in reviews, which lifts stars above 4.5 and raises share. Revenue per deal falls by about 2-3%, offset by volume.
- Risk: Margin on chairs and bookshelves shrinks. If the reserve + 5 floor is near 180 or 260, we may lose deals anyway. Matching the median also gives up the 'above the median' premium on buyers with high budgets.
- Honest: Only lowers real prices. No false claims, nothing sold below reserve + 5, no coordination with other shops.
- Test: Run it for one 15-minute meta_shop cycle on chairs and bookshelves only, leaving lamps unchanged. Compare deals per visit, mean price score in reviews, and stars against the previous hour. Roll back if deals per visit don't rise.

## 2. [shop] Tell the buyer the true market median when we close at or near it (small code)
- What: Add one factual sentence to the closing message and the 'too low' reply, generated from our own market-quartile data: 'Going price for chair over the last 3 hours: 180 (middle half 180-180). Your deal at 180 matches it.' Only include it when it is true, and skip it when our price is above the median.
- Evidence: Our reviews score price 3/5 'with median 180' because reviewers compare against the median. Reviews of other shops cite market averages ('Final price was 102 against a 103.8 market average'). 'Paid 180.0 of 205' and 'within budget' reviews show buyers cite measurable facts. Review quality drives buyer choice.
- Expected: Reviewers have the comparison in front of them. Price scores at or near the median should rise from 3/5 to 4-5/5, which lifts stars by 0.1-0.2 and ranking with review-driven buyers.
- Risk: Buyers may anchor their offers on the median and push us toward it. Extra tokens might slow the 8B model. If we're above the median, the line could draw attention to it.
- Honest: States a true public figure computed from market data. Doesn't create reviews or ask for a rating. We still only ask for an honest review.
- Test: Alternate the line on/off by visit parity for a few hundred deals. Compare average price score and stars between the two groups.

## 3. [buyer] Counter ask-heavy shops at the going median, and reuse each shop's observed floor (small code)
- What: Make the first offer 3-5% below the 3-hour median instead of a fixed share of budget. Walk away early only if the shop's floor, learned from our memory, is above the median plus 2%. Record each shop's final price per item and next time open there. Our budget-limited trades ended without a deal: chair budget 160 against a 180 going price, lamp budget 90 against 100. Skip those shops without wasting three offers.
- Evidence: 11 of the last 40 trades ended without a deal, listed as chair budget 160 and lamp budget 90 (walked) at fair-furniture, ggari-shop, cheapest-in-town and dragonash. Every one of those shops tells us its floor ('87 is below the market floor... Our final price: 100'; 'Counteroffer: 100'; 'chair at 180 (72% of budget)'). The 29 deals averaged 85.8% of budget.
- Expected: Fewer wasted offers and walks, a faster cycle, and lower prices on the deals we do make; the buyer's average paid share should move below 85.8% of budget.
- Risk: A low opening offer can sour a shop. Walking early can skip a shop that would have discounted. A stale remembered floor could be wrong.
- Honest: We offer a lower price within our own budget and never misstate our budget or the market. Reviews stay factual.
- Test: Compare mean paid / budget and the no-deal rate over the next 40 trades against these 40, per shop and item.

## 4. [shop] Make the deal receipts cover the repeat-visit benefit we already give, in plain terms (small code)
- What: In the closing message, state the loyalty code and next-order discount in one short line with exact numbers from our discount table and stamp card ('Code X, tier N: Y% off your next order; 8th order +8%'). Do not add urgency or a 'was' price. The code is already issued; this only makes it easy to use.
- Evidence: 11im (stars 4.59, 9.9% share) closes with 'Receipt No. ...: mention it on your next visit for an extra 3% off', and lee-sungsoo gives 'Your LSS stamp: LSS-MQLGNSRP' with a clear redemption rule. Both put the repeat incentive in the same line as the deal. Our visits-to-deals is 2577/3449 (75%), below dragonash (79%) and lee-sungsoo (79%).
- Expected: Higher repeat visits and a 1-3 point rise in share from returning buyers, with no change to the first-sale price.
- Risk: Discounts reduce margin on repeat orders; the discount table must never take us below reserve + 5. Longer messages might confuse the 8B model.
- Honest: Describes a real, signed, one-time discount we already give. No false scarcity or deadline.
- Test: Count returning-buyer visits per 100 deals, and the code redemption rate, for 24 hours after the change versus the 24 hours before.
