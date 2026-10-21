# Ideas, 2026-10-21 22:29:10 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Open at the going price, fixed and firm (knob)
- What: Set opening ask equal to the market median (bookshelf 260, chair 180, lamp 100) with target equal to the ask. Keep the reserve+5 floor. Reply with the exact number to close, as lee-sungsoo and 25620027 do, so a buyer can accept in one message.
- Evidence: lee-sungsoo (17.8% share, 4.43 stars) asks 260/180 and 'a single reply of yes closes the deal'; our chair reviews show 192->190->185 negotiations, and one 2* review says we held 185 against a 160 budget. Going chair median is 180 with middle half 180-182, yet we close at 185-192.
- Expected: Higher win rate against buyers who skip shops above the upper quartile, fewer walk-aways; deals and share up, small per-deal margin loss (~3-5%) on chairs.
- Risk: Gives up margin on buyers who would have paid more. Matching median may not beat shops that already match it.
- Honest: Only lowers our own asking price. Every quote is a price we actually charge, and no claims are made about others.
- Test: A/B by 15-minute meta_shop cycles: alternate current opener vs median opener per item for 2 hours. Compare deals/visits, average price, stars.

## 2. [shop] Truthful 'quality' line in the ad per item (small code)
- What: Put one verifiable product fact (material, warranty, assembly) into the opening message only if the shop really offers it. For example state the exact-price signed receipt and the loyalty code mechanism, which we already provide. Do not invent warranty terms.
- Evidence: lee-sungsoo's top-share replies state '2-year warranty', 'solid hardwood', 'signed receipt on closing'. nocha12 and JangSeongWoong got 3* reviews saying 'quality didn't impress me'. Our visit-to-deal ratio is 699/1055 (66%) versus lee-sungsoo 77% and dragonash 79%.
- Expected: Visit-to-deal rate moves toward 75%; marginal stars gain.
- Risk: If the warranty or material claim is not backed by what the shop actually offers, it becomes a false claim; the line is only usable with real terms.
- Honest: Only states facts the shop can honor, and the signed receipt is already what we issue.
- Test: Compare conversion (deals/visits) for the 3 hours before and after the template change, same items.

## 3. [buyer] Open with a spread-aware first offer near the going median (small code)  **FLAGGED: pays above the budget**
- What: For each item, make the first offer at about 95-97% of the going median (bookshelf ~250, chair ~172, lamp ~96) instead of budget-proportional, then raise up to 40% of the gap, and never go over budget or 1.1 x upper quartile. Walk only after three offers.
- Evidence: Going prices have very tight middle-half bands (chair 180-182, lamp 100-103) and top shops like dragonash and lee-sungsoo close at 180/260/100. Our buyer paid only 84.1% of budget on average but 10 of the last 40 trades ended without a deal, several 'walked' at budget 160 on chair against a 180 median.
- Expected: Fewer pointless walks, with price paid near the market median; deal rate up from 75%.
- Risk: A low budget (160 for a chair) cannot reach 180, so those trades stay no-deals; a median-based opener could overpay buyers with budget slack.
- Honest: Offers are our own real bids within budget, no false claims about other offers.
- Test: Run 40 trades with the new opener vs the last 40; compare deal rate and price/median.

## 4. [buyer] Skip shops with a no-deal history for the same budget band (small code)
- What: Add to buyer memory a per-shop, per-item record of walk-aways at a given budget. When the budget is below the shop's lowest recent deal price for that item, route to a shop that has closed at or under that budget (e.g. ggari-shop, psyoung82, corner-store lamp at 105) instead of trying shops that held the floor.
- Evidence: Our buyer walked at lee-sungsoo chair budget 160 twice, dragonash chair 160, fair-furniture bookshelf 230 twice. 11im lamp budget 110 got None. These repeated failures cost trades, while public ledgers show deals at various prices.
- Expected: Fewer repeated walks; deal count up; no change to price paid.
- Risk: Ledger data may be stale; fewer trials of cheaper shops could hide price changes.
- Honest: Uses only our own memory and the public ledger; no deception.
- Test: Count repeated (shop, item, budget) walk-aways in the next 40 trades versus 10 now; track deal rate.
