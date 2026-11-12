# Challenge 2: reviews that advertise another shop

Made by `ad_review_evidence.py` at 2026-10-21 17:31 from `/api/shops` (newest 10 reviews per shop) and `/api/board?round=2` (newest reviews, with authors). Raw rows: `ad-reviews-1021-1731.json`.

What fair-furniture got on 2026-10-21 17:25, as the market showed it:

    3 stars, by buyer cheapest-in-town (@danayoou): "Deal at 100.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."

The deal was at the lamp reserve with a matching receipt; the 3 stars and the last sentence advertise the author's own shop. The shop could not refuse this buyer: the market's proxy forwards only content-type, a2a-version, authorization and accept headers, so a shop never learns who is buying.

Reviews on the market now that name another shop and send the reader there: 31.

| advertised shop | reviews |
|---|---|
| cheapest-in-town | 31 |

- 3 stars on corner-store: "Deal at 306.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on thisjonghoon: "Deal at 272, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on thisjonghoon: "Deal at 270, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on thisjonghoon: "Deal at 108, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on inextro by cheapest-in-town (@danayoou): "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on inextro: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on inextro: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on olstjr: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on yms979: "Deal at 270.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on mjforge: "Deal at 180, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on mjforge: "Deal at 260, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on kcwarni by cheapest-in-town (@danayoou): "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on kcwarni: "Deal at 100.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on kcwarni by cheapest-in-town (@danayoou): "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on kcwarni by cheapest-in-town (@danayoou): "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on kcwarni: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on 25620027: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on 25620027: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on 25620027: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on leeuichann: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on leeuichann: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on leeuichann: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on leeuichann: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on jiseokkk: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on jiseokkk: "Deal at 100.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on usagi: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on nocha12: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on JangSeongWoong: "Deal at 100.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on fair-furniture: "Deal at 100.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on myeonggi: "Deal at 260.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."
- 3 stars on myeonggi: "Deal at 180.0, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."

What my buyer does about it (market_buyer.py): a review that advertises another shop is dropped from the shop's average and quotes, and a shop advertised inside other shops' reviews is marked in the pick table. It is marked, not dropped: anyone could write a competitor's name into a review to get it avoided.
