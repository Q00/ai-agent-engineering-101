# Ideas, 2026-10-20 21:20:17 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Quick close near market median on first counter (small code)
- What: Change the concession rule so that when a buyer's offer is within ~5% of the quartile median for that item (e.g. chair >=180, lamp >=100, bookshelf >=262), the shop accepts or counters at a price just above reserve+5 instead of conceding only 40% of the gap. Cap rounds at 2 so the deal closes quickly, with a signed receipt.
- Evidence: Our shop has 20 deals out of 28 visits (share 1.1%) and 3 of 8 sampled reviews are 'Could not agree on a price' (2*). Top shops close fast: dragonash 'Deal: bookshelf at 266' after 2 offers, ggari-shop 'Fair deal at 180 after 2 counters', and 11im sells lamps at 103-105 with counters of 105/107. Our buyer closes dragonash bookshelves at 266-268 after counters of 4-5 steps.
- Expected: Fewer failed negotiations, so more deals per visit (maybe 71% to 80%+) and fewer 2* 'could not agree' reviews, lifting stars toward 4.0. Slightly lower average price, offset by volume.
- Risk: Margin shrinks on buyers who would have paid more; the acceptance band may be set too low if the market drifts.
- Honest: Prices stay at or above reserve+5, no false claims are made, and the buyer is simply offered a fair market-level price sooner.
- Test: A/B by alternating the concession rule per 15-min meta_shop cycle; compare deals/visits, share of 'could not agree' reviews, and average margin over ~100 visits each.

## 2. [shop] Open the ask nearer the market median (no padding) (knob)
- What: Set opening ask per item to roughly the median plus ~3% (chair ~195, lamp ~108, bookshelf ~283) rather than the current quartile-based opening, and keep the target at the median. State the ask plainly without invented 'was' prices.
- Evidence: outseop-26510122 got a 3* review: 'The shop opened at 202, above the 199.2 market average... so its opening ask was padded.' Meanwhile ggari-shop opens chair at 204 and lands 184, while kimauve, with medians 105/180/260 close to market, converts 126/156 visits (81%). Our visit-to-deal rate is 71%.
- Expected: Better conversion and fewer reviews calling the ask padded; modestly higher stars and share; average price drops a few percent.
- Risk: Leaves money on the table with buyers who accept the opening ask; if market medians fall we may chase the price down.
- Honest: Opening is a real price we'd honor, with no fake anchor; it is just closer to the true market level.
- Test: Change the opening-ask knob in meta_shop.py for half of the cycles; track review text mentioning padding or 'could not agree', deal rate, and average sale price.

## 3. [buyer] Open higher on the first offer to cut rounds (small code)
- What: Set the buyer's first offer to ~96-97% of the shop's median price (e.g. dragonash bookshelf ~262 rather than 255-262, chair ~175) and raise to the shop's last ask, instead of climbing in small steps. Still never exceed budget or 1.1x the upper quartile.
- Evidence: Our buyer's dragonash bookshelf trades take 4-5 offers: offers [262,266,267,268] and [255,264,267,268,268], finishing at 266-268 while the shop 'sold at 260 to others'. Final prices barely differ from the first offers, so extra rounds add cost and error exposure (kimchyoungman lamp ended in 'error').
- Expected: Same final price within ~1%, fewer rounds, less chance of errors or time-outs, and a slightly higher deal rate.
- Risk: A higher opener could raise the final price a little for shops that anchor on the first offer.
- Honest: The offers are real and within budget, with no false claims; we just offer a sensible price earlier.
- Test: Compare mean rounds, final price as a share of budget, and walk/error counts over the next 40 trades versus the current 86.4% paid share.

## 4. [buyer] Add cheaper shops to the shortlist for low budgets (small code)
- What: When a budget is below the going median, route the buyer first to shops with the lowest medians for that item (outseop lamp 100, ggari-shop lamp/chair 100/180, kimauve chair 180) and spend the three offers there, rather than walking after a fixed set. Keep the skip rules for cheats and unsigned shops.
- Evidence: Five trades ended with no deal: olstjr lamp budget 90 (walked), 11im lamp budget 90 (walked), 11im chair budget 160 (walked), ggari-shop chair budget 160 (walked). Going prices are lamp 100-115 and chair 180-205, all above those budgets, while outseop-26510122 has a lamp median of 100 and kimauve a chair median of 180.
- Expected: Fewer walks and higher completion rate for low-budget orders; some still won't close because the budget is below the market.
- Risk: Lowest-median shops may have lower stars or more load; some budgets truly can't be met, so walking remains right.
- Honest: Buyer only buys within budget and stays honest in reviews; it reports no-deal outcomes factually.
- Test: Track the share of low-budget orders that complete, average paid vs budget, and count of walks over the next 40 trades.
