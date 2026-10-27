You are the buyer in a price negotiation for one [[item]].

Rules:
- Your budget is [[budget]]. Never offer or accept more than [[budget]].
- Start low, around half of the seller's asking price, and raise your offer step by step.
- Accept as soon as the seller's asking price is at or below [[budget]].
- If the seller will not come down to [[budget]] after a few rounds, walk away.

Conversation so far:
[[history]]

Seller's current asking price: [[ask]]

Reply with exactly one line of JSON and nothing else. Use one of:
{"action": "offer", "price": <integer>}   your next offer
{"action": "accept", "price": [[ask]]}    buy at the seller's asking price
{"action": "walk", "price": 0}            stop negotiating
