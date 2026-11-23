# Before adaptive pricing

Captured on 2026-10-25, before changing the seller's pricing policy.
The timestamped JSON files preserve the public board and reviews as received.

Shop: Afford Shop. GitHub owner: Tulga0419. Market round: 2.

| Measurement | Before change |
|---|---:|
| Counted deals | 4 |
| Visits shown on board | 11 |
| Share returned by API | 0.0 (rounded; not proof of exactly zero) |
| Reviews | 4 |
| Average stars | 3.75 |
| Revenue shown on board | 1080 |
| Average revenue per counted deal | 270 |
| Shop alive | false |

All four available reviews describe bookshelf trades. They report an opening
ask of 338 and final prices of 290, 270, 260, and 260, averaging 270.
These prices are review-reported, not independently recovered receipt data.
They agree with the board's aggregate revenue divided by its deal count.
No separate lamp or chair average is available from this snapshot.

The reviews say receipts matched the deals. None explicitly says the asking
price was unfair. One gives three stars, but does not explain that rating.
We must not describe these reviews as price complaints.

The saved market-kit log has seller HTTP access lines, but no seller-side
deal records with prices. Buyer trade memory describes purchases from other
shops and must not be used as Afford Shop's own sales history.

Next: add structured seller outcome history and feed that history plus its
own reviews into opening-price decisions. Keep reserves fixed. Compare a
new observed trading window with this baseline, noting the offline interval,
different traffic, and cumulative/rounded board metrics as limitations.
