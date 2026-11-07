# Seller history step

The seller now appends its own observations to `logs/seller-history.jsonl`.
Each JSON line contains the time, shop, task ID, item, opening ask, current ask,
policy name, and event. Offers and completed local deals also record their price.
Decision events include the harness's reason.

Events are `opened`, `offer_received`, `countered`, `deal`, `rejected`, and
`canceled`. Group lines by task ID to follow a negotiation. An opening without
a final event is unfinished, not evidence that the buyer rejected the price.
A local deal is not proof that the market accepted or verified it.

The asking-price strategy is still the original 30 percent above reserve.
This step only collects the evidence needed for the next pricing change.
Old market logs and the before-change baseline are preserved.

Offline checks use simulated decisions and a temporary history file. They do
not add trades to the real seller history. The saved check output includes four
history checks and the existing receipt-signature check; all five passed.
PowerShell formats Python's stderr as a NativeCommandError record in the saved
console output, but unittest reports OK and the Python exit code was zero.

To keep later local runs separate from market observations, start the seller
with `--history logs/challenge-1/local-test-history.jsonl` when testing manually.
The default history also records official test traffic if a test starts the
normal seller command; such runs must be identified before comparing market data.
