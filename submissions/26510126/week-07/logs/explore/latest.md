# Ideas, 2026-10-21 08:24:09 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Open at the market floor on items where rivals hold the floor (knob)  **FLAGGED: sells below the reserve**
- What: In meta_shop.py set opening ask equal to target equal to the observed middle-half low (bookshelf 260, chair 180, lamp 100) instead of a higher opening ask with concessions. Keep the code rule of never selling below reserve + 5. Keep the opening line short and factual: 'Asking 260, signed receipt, no added fees.'
- Evidence: Top shops lee-sungsoo (21.2% share) and nocha12 open and hold at 260 and say 'one reply accepting it closes the deal'. 11im sells at 260-263. Our reviews: 2* 'Shop refused to budge from 265 despite three reasonable offers' and 2* 'Could not agree on a price', while going median is 260.
- Expected: More closed deals per visit (our 271 deals / 441 visits = 61% vs 11im 79%, lee-sungsoo 78%), fewer 2* no-agreement reviews, higher share; per-deal price drops about 2%.
- Risk: Margin shrinks if reserve is near 260. Buyers with high budgets pay less than they would have.
- Honest: The ask is a real price we actually charge, with no false 'was' price. We never go below reserve + 5.
- Test: A/B by item across 15-minute meta_shop cycles for 24h: floor-open vs current. Compare deals/visit, stars and revenue per visit.

## 2. [shop] Reply with a single final price once the buyer is within a few units of it (small code)
- What: Add to the shop code: if the buyer's offer is within about 2% of the target, answer with one clear 'Deal at X' or 'Final price X, reply accept' instead of another 40%-of-gap counter. Counters stay at X (no endless rounds). Cap rounds at about 3.
- Evidence: lee-sungsoo's reviews show 'deal after 8 offers' and its replies are one-line 'lowest price, one reply accepting it closes the deal'; our 2* review 'Could not agree on a price' and the 429 failures show long negotiations get lost. Buyers' own review: 'nocha12 ... 3 offers until the end'.
- Expected: Fewer abandoned negotiations, fewer timeouts under rate limits, deals/visit up toward 75-80%, small star gain.
- Risk: Some buyers who would have paid more are closed early; a firm line may cost a few units.
- Honest: States a true final price and honors it. The shop never pressures with fake deadlines or scarcity.
- Test: Compare mean offers per deal, deals/visit and 1-2* share before and after the change for 24h.

## 3. [shop] Show receipt number with a real, honored repeat-visit discount (small code)  **FLAGGED: ties a reward to reviews or stars**
- What: Include the signed receipt number in the deal message and tell the buyer that quoting it on the next visit gives X% off, applied through our existing loyalty code table. Do not ask for or condition anything on reviews in the same sentence.
- Evidence: 11im (21.7% share) says 'Receipt No. ...: mention it on your next visit for an extra 3% off' in every deal message. We have loyalty tiers and one-time codes, but our codes are not surfaced as a simple receipt-number mention.
- Expected: More repeat visits and a higher share of loyalty-tier orders; share growth over several hours. Cost about 3% on repeat deals.
- Risk: Discount may cut into margin below reserve + 5; buyers may not remember to mention it.
- Honest: A genuine, honored discount tied to a true receipt; no review conditioning. Dropping the 'please leave a review' line from the same message keeps the discount fully separate from reviews.
- Test: Count visits that quote a receipt number, repeat-buyer rate, and margin on those deals over 48h versus the control period.

## 4. [buyer] Open near the market floor and walk less on tight budgets (small code)
- What: In the buyer, start the first offer at the lower middle-half price for the item (lamp 100, chair 180, bookshelf 260) when the budget is at or above it. When the budget is below every price, skip the 3 offers and move directly to the next shop whose ask is lowest in the ledger, rather than walking from the same shop. Do not retry the same shop twice in a row where it walked.
- Evidence: Our last 40 trades: 11 no-deals, mostly 'walked' at lee-sungsoo (chair budget 160, bookshelf budget 230 twice) and dragonash chair 160, while the going median chair is 180 and bookshelf 260; those budgets are below every price. Also the buyer paid 86.1% of budget on average.
- Expected: Fewer wasted turns and fewer rate-limit failures; no-deal rate down from 27% as budgets below the median are matched to shops that actually concede (e.g. corner-store, yms979, leeuichann closed at 100 on a 105 budget).
- Risk: Budgets under the market price may have no seller at all; then walking is correct and the number of no-deals will not fall.
- Honest: We offer only within our true budget and never reveal a false budget; skipping shops is not deception.
- Test: Track no-deal rate and mean paid/budget over the next 40 trades against the previous 40, split by budget below or above the median.
