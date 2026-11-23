# Round 3 operational adapter plan

The selected experimental policies remain decision-only. `agent.json`, the running Seller/Buyer, and `safe_restock.py` are unchanged.

## Boundary

- Seller adapter: reads kit-shaped inventory/catalog/order data, selects a real unit, and proposes B-safe pricing. The operating Seller must still create and sign its receipt.
- Buyer adapter: uses B-original price ranking, rejects explicitly invalid evidence, and returns a kit `accept` policy. Unknown advertised evidence is not trusted; the kit must verify the real market certificate, signed checkout JWT, budget, ACP checkout, and AP2 mandates.
- Restock adapter: defaults to exact `{"buy":[]}`. Preview computes bounded candidates but deliberately keeps `buy` empty. Enabling purchases is not implemented here.
- Mock certificate flags are merely test fixtures. They are not cryptographic certificates and must never be promoted into production evidence. The adapter tests call the official kit's `check_accept` with a generated test key to verify invalid-certificate rejection.

## Model, context, tools

- Model: current price, stock, and shop decisions are deterministic rule-based policies, not an LLM. An optional model could later propose bounded adjustments, but deterministic guards must remain authoritative.
- Context: local history should store settled/failed outcomes, prices, receipt mismatches, warranty incidents, and timestamps. Only past records may influence decisions.
- Tools: inventory selection, pricing, certificate/checkout verification, signing/payment, restocking, and history persistence remain separate. Cryptographic and payment tools should reuse kit v4 rather than be reimplemented.

## Required before deployment

Validate actual catalog/inventory/demand examples, map A2A messages to quote/counter/accept, integration-test signed receipts and ACP/AP2 in the official test environment, confirm forum/star fields and rate limits, then review a restock preview before explicitly adding an active mode. SAFE remains the default throughout.
