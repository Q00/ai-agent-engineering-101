# Quality and cap: unconfirmed market change

Source: the user's 2026-10-27 class update. The supplied diagram illustrates the
existing ACP/AP2 flow; it does not define quality fields, cap values or scoring.
This is a scenario note, not a verified API contract or an implemented quality rule.

HTML mapping: L2 checkout/mandate fields, R3 settlement validation, WATCH pricing,
and HW comparison. Existing item-only prices and scoring remain the confirmed baseline.

| Possible meaning | Strategy implication | Confirmation needed |
| --- | --- | --- |
| Quality ceiling per item/shop | Do not claim quality beyond the authorized ceiling | Source, scale, owner, per-item/per-shop scope |
| Finite high-quality inventory | Consider stock opportunity cost across future orders | Replenishment, reservation, cancellation, cost |
| Quality-dependent price ceiling | Enforce a price ceiling only if the market actually defines one | Formula, floor compatibility and settlement checks |
| Buyer minimum quality | Filter eligible quality before optimizing price | Signed order requirement and authoritative quality proof |
| Quality changes ranking rewards | Recompute objective from official score, rather than invent q/price | Saved/Profit changes and authoritative weights |

Preparation in this correction:

- Durable experience has a contract-scope namespace. Old item-only evidence
  is kept in `course-kit-v3:item-only`; a future confirmed contract can use a
  different namespace without rewriting old facts or mixing their rewards.
- No quality number, quality cap, price ceiling or quality claim is invented.
- The public shop card and signed receipt remain compatible with the current kit.
- Any new quality variant must be traceable through order, receipt, checkout,
  mandate and settlement according to the official updated schema. Arbitrarily
  adding a signed field would break canonical signatures rather than implement it.
- A valid signature proves who signed the representation, not physical quality.

When the updated kit/README exists, read its exact contract, verify the quality
source and cap meaning, add fixtures for variant mismatch and cap violations,
and only then activate the new namespace and handling. Existing APIs alone do
not establish readiness for an unannounced future quality contract.
