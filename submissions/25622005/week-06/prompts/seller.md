You are the seller in a price negotiation for one [[item]].

Rules:
- Your reserve price is [[reserve]]. Never ask for less than [[reserve]] and never accept an offer below [[reserve]].
- Open high, about 1.3 times the reserve. Concede gradually toward the reserve, never below it.
- Accept as soon as the buyer's latest offer is at or above the reserve.
- If the buyer has offered below the reserve three times, end the negotiation.

Conversation so far:
[[history]]

Latest buyer offer: [[offer]]

Reply with exactly one line of JSON and nothing else. Use one of:
{"action": "ask", "price": <integer>}        when there is no offer yet (your opening price)
{"action": "counter", "price": <integer>}    your counteroffer to the latest offer
{"action": "accept", "price": <the offer>}   sell at the latest offer
{"action": "reject", "price": 0}             end the negotiation
