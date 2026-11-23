# Strategy interfaces and implemented baseline

These are experiment contracts, not official market-kit fields. They are now
implemented in `sim/policies.py`; baseline agents use them in `sim/agents.py`.
The transport, signature, checkout, and validation layers stay shared; only
pure decision functions change between strategies.

## Seller strategy

Input:

```json
{
  "scenario_id": "normal",
  "now": 1793664000,
  "order": {"item": "가구>조명", "acceptable": ["G-LAMP-01"], "budget": 110000},
  "eligible_units": [{"unit": "...", "sku": "G-LAMP-01", "cost": 62000, "grade": "A"}],
  "history": {"attempts": 0, "settled": 0},
  "buyer_offer": null
}
```

Output:

```json
{"action": "ask|accept|reject", "price": 100000, "unit": "...", "claim": null, "warranty_hours": 0}
```

Shared guards, outside every strategy, must reject missing/expired inventory,
unknown SKUs, dishonest claims, unavailable units, and invalid prices.

## Buyer strategy

Input combines the official current `order`, `shops`, verified quote/receipt
facts, and isolated past outcomes. Forum and review text is marked untrusted.

Output selects `shop`, whether to continue, a bounded offer, and an official
`accept` policy (`cert`, `min_hours_left`, `grades`, `warranty_hours`). Shared
guards enforce budget, certificate and receipt consistency.

## Restock strategy

Input is immutable `inventory`, `catalog`, demand descriptors, and the
strategy's own historical observations. Output is only:

```json
{"buy": [{"sku": "...", "qty": 1}]}
```

The shared guard caps quantities/lines/spend and returns `{"buy":[]}` on any
validation failure. Bankruptcy remains a separate explicit policy decision.

## Experiment runner and result

Each `(strategy, scenario, seed, repetition)` receives a private clone of the
same fixture. A result should record revenue, realized profit, ending stock
cost, settlement rate, honest-claim rate, stars proxy, buyer surplus, expired
loss, warranty refunds, invalid decisions, and deterministic event trace.

Before implementing strategies, freeze:

1. the precise scoring formula and horizon;
2. negotiation turn and timeout limits;
3. breakage assumptions (official probability is unpublished);
4. how mock forum reputation is scored;
5. strategy names and allowed information.

The current engine intentionally does not implement A/B/C/D, A2A negotiation
turns, cryptographic market certificates, ACP/AP2 signatures, real reviews, or
forum scoring. Those remain separate from the common local safety and
accounting layer.

