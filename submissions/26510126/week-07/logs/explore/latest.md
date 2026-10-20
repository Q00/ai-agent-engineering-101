# Ideas, 2026-10-20 21:19:36 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Price the opening ask near the median, not above it (knob)
- What: In meta_shop.py set the opening ask for each item at about the market median (lamp ~105-110, chair ~190, bookshelf ~275) instead of the upper quartile, and set the target at or just under the median. Keep the reserve + 5 floor in code.
- Evidence: Our shop has 20 deals from 27 visits (1.1% share), but the top shops with medians near the market (corner-store {'bookshelf': 290, 'chair': 195, 'lamp': 110}, outseop {'chair': 182, 'bookshelf': 262, 'lamp': 100}) win 15-29%. Our reviews include 'Could not agree on a price' (2*, twice) and 'No agreement within my budget'. Our buyer also skips shops above the upper quartile, so a high ask means we may be filtered out before negotiating.
- Expected: More visits convert to deals. Deal count and share should rise, with a slightly lower average price per deal. Stars should improve as fewer 'could not agree' reviews appear.
- Risk: Lower margin per deal. If the medians are already close to our current asks, the change will have little effect.
- Honest: Only changes the asking price, which is a real price we will charge. No false claims, and the reserve floor is unchanged.
- Test: Run for 2-3 hours against the previous 15-minute config. Compare conversion (deals/visits), 'no agreement' review share, and mean margin over reserve.

## 2. [shop] Open with a concrete, honest ask and a quick first counter (small code)  **FLAGGED: ties a reward to reviews or stars**
- What: Make the Llama reply always state the item, our actual current ask and a plain 'send your offer as a number'. Have the code generate the first counter by conceding toward the buyer's offer immediately (e.g. a counter within ~5% of the buyer's first offer when it is above the floor), and make the 'Deal: item at X' line a fixed template, with no free-form LLM text on prices.
- Evidence: Winners use short templated lines: corner-store 'Counteroffer: 306 / 275 / Deal: bookshelf at 267', 11im 'Counteroffer: 105 / 107 / Deal: lamp at 103', dragonash '260 is too low. Counteroffer: 270.' Reviews reward speed: 11im '5*: deal after 2 offers', ggari-shop '2 counters'. Others show 5 offers until the end, and 11im has a 2* review for an API failure. Our 8B model may add latency or errors.
- Expected: Fewer rounds per deal, fewer errors and timeouts, and a higher conversion rate. Stars should rise slightly from 'quick and fair' reviews.
- Risk: Conceding faster can leave some money on the table with buyers who would have paid more.
- Honest: Every number quoted is a real counter we would accept. Nothing is claimed about other buyers or deadlines.
- Test: Log rounds-to-deal, error rate and conversion before and after. Check the review text for 'quick', 'fair' and 'error'.

## 3. [buyer] Open higher on the first offer to cut the round count (knob)
- What: Our buyer currently ends up paying near the ask anyway (dragonash bookshelf: asks [282,272,269,268], offers [262,266,267,268]). Instead, start the first offer at ~92-94% of the shop's median (not the 255-262 we use now) and raise by small steps. Stay within budget and the 1.1 x upper-quartile cap.
- Evidence: Our buyer pays 266-268 on dragonash bookshelves every time while 'it sold at 260 to others'. Offers of 255-262 never move dragonash below ~266, so the low opening gains nothing and costs 4-5 rounds. We also had 5 walks, such as 11im lamp budget 90 (walked) and 11im chair budget 160 (walked), where the budget is below every price.
- Expected: Fewer offers per trade with about the same final price. Better success rate where the gap is small. It is mainly a speed and reliability gain.
- Risk: A higher first offer might cost 1-3 units on shops that would have conceded more. It does not help when the budget is under every price.
- Honest: Offers are real and within budget. Only the opening level changes.
- Test: A/B the opening ratio over the next 40 trades. Compare average paid/budget (currently 86.8%) and offers per deal.

## 4. [buyer] Prefer shops whose public price level fits our budget before negotiating (small code)
- What: Before the first offer, rank candidate shops by their published item median from the ledger. If the budget is below the shop's median for that item, skip it up front and go to a cheaper shop, such as outseop (lamp 100, chair 182), ggari-shop (lamp 100, chair 180) or kimauve (bookshelf 260, chair 180). This avoids three-offer walks.
- Evidence: Walks: 'olstjr lamp budget 90 (walked)', '11im lamp budget 90 (walked)', '11im chair budget 160 (walked)', 'ggari-shop chair budget 160 (walked)'. These budgets are below the medians of those shops (11im lamp 104, chair 186; ggari-shop chair 180). Cheaper shops with deals at the budget exist in the table.
- Expected: Fewer no-deal trades (5 of 40), and some wasted rounds avoided. A modest rise in success rate.
- Risk: A low-budget order may have no affordable shop at all. Median data can be stale, so some cheaper shops may be skipped incorrectly.
- Honest: Only uses public ledger data and our own budget, and says nothing untrue about the shops.
- Test: Count walks per 40 trades, compared to the current 5. Check that reviews written remain factual.
