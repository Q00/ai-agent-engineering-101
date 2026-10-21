# Ideas, 2026-10-22 06:32:14 (sonnet)

Proposals only: nothing here is applied until a person reviews it.

## 1. [shop] Price-match floor on lamps at the market median (knob)
- What: Lower the lamp target and the shown floor to 100, the going median, with the reserve + 5 rule still in force. If a buyer's stated budget sits at or above reserve + 5 but below our current floor of 105, accept it instead of refusing. In meta_shop.py, clamp lamp target to min(quartile-derived target, 100) when reserve + 5 <= 100.
- Evidence: Our 2* review: 'Shop refused to negotiate below 105 despite customer reaching their 90 budget limit'. Going prices: lamp median 100 (middle half 100-105). dragonash, lee-sungsoo, mjforge and psyoung82 all close lamps at 100, and yms979 shows 100 as its floor.
- Expected: Fewer lost lamp deals and fewer low-star reviews. Share should rise a little, since lamp visits that walk on 105 would convert at 100. Margin per lamp drops by up to 5 credits.
- Risk: If the lamp reserve is near 100, margin disappears. Buyers with a budget under 100 still walk.
- Honest: Real prices only. It never sells below reserve + 5 and never exceeds the buyer's budget, and no false claims are made.
- Test: Compare lamp deal rate (deals/visits) and lamp review stars for 24h before and after, using the per-item split of our 1763 visits and 1233 deals.

## 2. [shop] Short, copyable exact-price close line (small code)
- What: Add to the shop's reply, once the price is within the concession range, a plain line such as 'Reply with exactly N for a signed receipt at N.' N is our real current accept price. Prefer to put it in code (template) rather than leave it to Llama 8B, so the wording is consistent.
- Evidence: mjforge: 'reply with 100 alone for instant signed receipt'; 25620027: 'Reply with exactly 262 to complete the purchase'. Both shops are in the top 8 and their reviews praise exact-price receipts. Our own visits-to-deals ratio is 1233/1763 = 70%, versus dragonash 8063/10220 = 79%.
- Expected: Fewer negotiation rounds and fewer buyers who stall or walk after three offers. Deal rate could move from about 70% toward 75-79%, which means more share.
- Risk: Buyers who would have haggled lower pay the stated price. This is minor, because the stated price is already our accept price.
- Honest: N is a real price the shop will honor, and the receipt is signed at N. There is no urgency or scarcity claim.
- Test: A/B by visit parity for 6h each. Measure deal rate per visit and the average number of messages per deal.

## 3. [buyer] Stop re-visiting shops that already caused a walk (small code)
- What: Add a per-shop, per-item memory of walks. After one walk caused by the budget being below the shop's floor for that item, put that shop on a cooldown for that item (e.g. 3 hours, or until its reported median moves). In the meantime, route the buyer to shops whose medians fit the budget, such as dragonash or lee-sungsoo for lamps at 100.
- Evidence: Buyer walks: fair-furniture chair budget 160 walked three times, plus fair-furniture lamp 90, dragonash chair 160. Chair going price is 180 (middle half 180-183), so a 160 budget is below every shop. 9 of 40 trades ended without a deal.
- Expected: Saves wasted trips and calls, raises deals from 31/40, and avoids repeat bad outcomes. It does not change the average paid fraction (84.1% of budget).
- Risk: A shop may lower its price later, and the cooldown would miss it. Some budgets are unreachable anywhere, so no routing helps.
- Honest: It only changes which shop we pick, using our own memory and public data. It stays inside budget and does not touch other agents' load.
- Test: Compare the share of buyer trades ending in a deal, and the number of visits per deal, over the next 40 trades against the last 40.

## 4. [buyer] Anchor first offer at the shop's quartile-low and use fewer concessions on cheap shops (knob)
- What: Open at about 95% of the item's going median (chair 171, bookshelf 247, lamp 95) when the buyer's budget allows, and keep the 40% gap raise rule. Where the budget is below the going median, skip negotiation at once and pick a different shop instead of spending three offers.
- Evidence: Buyer pays 84.1% of budget on average. dragonash counters 255 with 260, 90 with 100 and 180 with 181, so openings 3-10% below median still reach a deal at median within a round or two. ggari-shop review: 'Deal at 180 for a chair (asked 200) after 4 offers', so a patient, lower opener got a below-ask price.
- Expected: Lower average price paid, perhaps 1-3% on deals where shops sit above the median. Deal count stays flat for shops that counter near median.
- Risk: Low openings can cost an extra round with rigid shops, and a budget-limited buyer may hit the three-offer limit.
- Honest: All offers are real offers within budget. No claim is made about other prices, and the review we write stays factual.
- Test: Run on half of buyer tasks for 40 trades and compare the paid-to-median ratio and deal rate with the control half.
