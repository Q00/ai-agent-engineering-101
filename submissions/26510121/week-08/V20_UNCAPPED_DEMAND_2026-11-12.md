# v20: inventory unit caps removed; demand counterfactual

The owner's instruction is to remove inventory quantity ceilings and determine
whether demand inference would still keep stock low. This release changes the
quantity contract; it does not force spending or assert that more stock wins.

## Executable change

`shop_inventory_caps_enabled=false` removes the total held-inventory ceiling,
the per-SKU held ceiling, the twenty-unit demand-target ceiling, sparse-data
two-unit and public-data six-unit target ceilings, and the fixed one-unit probe
batch rule. The old numerical configuration values remain only for explicit
legacy replay. They have no held-inventory authority in the new mode. The
feedback compiler no longer publishes old unit-cap patches in this mode.

The official twenty-unit maximum remains a limit **per supply POST**. A plan can
request up to twenty units of a SKU even when more than twenty are already held,
and later legal requests can replenish it again. Prepared plans still reduce
their quantity after previously planned units arrive, and uncertain supply POSTs
are never blindly retried. The ten-line planning/time budget remains an execution
constraint, not an inventory holding limit.

Cash affordability, full refund collateral, storage reserves, category and SKU
capital concentration, exploration cash allocation, supply windows and signed
checkout checks remain. No Buyer strategy, identity, balance, ledger or payment
history is replaced. The Sol/Terra/Luna model allocation is unchanged; Astra stays
excluded. This work made no paid model validation calls.

Two numerical fixes are necessary when caps no longer hide boundary failures:

- Poisson tails are summed from the appropriate boundary rather than starting
  from `exp(-lambda)`, which underflows for large arrival means. Demand quantiles
  use binary search and can exceed twenty without an artificial unit ceiling.
- An exploration prior is applied once to an arrival process. Additional units
  and existing held units receive diminishing sell-through probability. A fixed
  probability cannot be repeatedly assigned to every new probe unit. Unknown
  demand retains the explicit first-unit discovery prior; it is not treated as
  known-zero demand. This prior is an engineering assumption, not observed demand.

## Same-facts result

Cutoff: the frozen source saved at 2026-11-12 14:26:19 KST. Own stock was
157 units across 97 SKUs. All scenarios use the same inventory, prices, exposure,
competitor observations and cash; the supply window is hypothetically open and
visibility is hypothetically healthy. **No scenario placed a trade.**

| Scenario | Planned lines | Planned units | Capital commitment | Expected Book scenario |
| --- | ---: | ---: | ---: | ---: |
| Existing v19 | 10 | 13 | 1,992,214 | 97,014 |
| Remove quantity caps only | 10 | 13 | 1,992,214 | 97,014 |
| v20 with diminishing probe probability | 10 | 13 | 1,992,214 | 97,014 |
| Hypothetical arrival rates x2 | 10 | 12 | 15,252,664 | 1,101,011 |
| Hypothetical six-hour target coverage | 10 | 33 | 84,259,618 | 2,945,260 |

All 97 held-SKU targets were unchanged by cap removal. **82 of 97 still had a
target of one or two units.** Thus removing caps alone does not explain or solve
the current small-batch behavior. The rate-x2 case also shows that stock count
alone does not measure economic opportunity: its portfolio changes composition
and capital, while its total planned unit count is lower.

The last two rows alter assumptions. Their larger Book forecasts are **not**
measured or identified gains and are not promoted as operating policy. The
six-hour target case is a sensitivity test; the current normal own-repeat target
coverage is at most three twenty-minute cycles, with at most two cycles for
qualified public-only evidence and one cycle for unqualified evidence.

## Demand-model diagnosis

Verified mechanisms, distinct from proof of understated true demand:

1. The external-sales prior is `0.5 * SKU sales / 6 hours / (competing sellers +
   us)`. Equal traffic share and the factor 0.5 are conservative assumptions.
   They are not measurements of this shop's obtainable orders.
2. Only 22 of the 97 held SKUs had at least three profitable own payments in the
   frozen evidence. Most SKUs therefore rely on public or request-conversion
   inference and shorter target coverage. Low inferred arrivals produce small
   Poisson targets without any unit ceiling.
3. Realized settlements omit unserved demand. `lost_exact_requests` is recorded
   but is not directly added to the numerical SKU arrival rate. Eligibility,
   reservations, shelf life and price failures must be separated before treating
   those events as inventory-recoverable demand.
4. Physical availability exposure is not a complete measure of publicly reachable,
   unreserved and term-eligible exposure. Offline and reservation effects can bias
   interpretation; simply doubling rates would not resolve that identification.
5. The model's time horizons and assumptions differ: inventory target coverage is
   around 20–60 minutes, while marginal sale economics use up to six hours.
   Longer coverage materially changes the sensitivity result. Its reliability
   should be checked against actual supply access, expiration and future payments.

The frozen six-hour source had 16,673 public trades and 1,000 customer visits,
below the current read limits of 20,000 and 2,000 respectively. Truncation is
therefore **not established as the current cause**. The 120/hour arrival-rate
clamp is another model assumption, not a market inventory holding limit.

The release adds `demand_diagnostics` to the background objective context:
cap state, low-target SKU count, own-repeat evidence, target shortfall, coverage
period and rate-source examples. Research sees the distinction before Main acts;
the field is bounded and carries no execution authority. Initial batch limits
remain in the legacy research schema but are explicitly declared non-executable
in uncapped mode. No extra LLM call is added to Main.

## Evidence and validation

- Frozen source: `logs/v20-frozen-demand-20261112T052619694729Z.json.gz` (5.07 MB,
  compressed immediately; no oversized raw companion).
- Scenario output: `logs/v20-demand-counterfactual-20261112T052634243580Z.json`.
- Exact queries/calculations: `validate_uncapped_stock.py`.
- Context size contract: `logs/v20-context-contract-20261112T053139693587Z.json`.
  The contract uses an explicitly historical envelope to test structure/size;
  it is not a mixed-date live diagnosis.
- **420 tests passed**, one existing Starlette deprecation warning. Final
  console: `logs/stage-v20-full-regression-03.txt`.
- Regressions include stock above the old total and SKU caps, a legal twenty-unit
  request when twenty-five are held, prepared-plan acquisition deduplication,
  unaffordable cash rejection, high-mean Poisson stability and diminishing probe
  probabilities. Explicit legacy-mode tests retain the old contract.
- The first full test attempt exposed missing treatment of unknown probe demand
  and legacy-test expectations. Its seven failures remain in
  `logs/stage-v20-full-regression-01.txt`; subsequent tests and changes are retained.
- Source checkpoint: `logs/v20-release-candidate-20261112T053247490204Z.json`.

The current frozen portfolio is unchanged, and the new boundary behavior is
tested. Future realized profit, ratings and rank superiority are not proven.
Deployment and live verification are recorded below.

## Deployment

Source promotion completed at **14:34:41 KST**, manifest
`logs/v20-source-promotion-20261112T053441246238Z.json`. Buyer was drained before
negotiation/payment; the old watcher and runtime released their leases. Identity,
credentials, cash, stock, reservations and ledger were preserved. New runtime:
PID 15636, session `6689840b259d4bf8989670719a3bff4e`.

The source/config check confirms `inventory_limit=None` and unbounded demand
targets in the new mode. Startup public tunnel readiness and fresh background
demand diagnostics are checked separately; a source copy is not connectivity
proof. Supply is currently closed, so actual new supply execution is not yet
evidence for this release's quantity behavior.

## Live verification, 14:38:03 KST

- `logs/v20-live-verification-20261112T053803215823Z.json`: both accounts online,
  all four public HTTP checks 200, no source mismatches, recovery guard active.
- `logs/v20-live-cap-proof-20261112T053721444314Z.json`: unit caps disabled,
  `inventory_ceiling=null`, fresh demand diagnostics and a recent supervisor
  heartbeat. Since restart it recorded one Shop sale and three Buyer purchases,
  zero analytical, context, supervisor, supply, worker or deferred-research errors.
- A completed live research review references objective context
  `b507a834b4ab2cc1e57a30bef14a472974c395b5536ee497c4366893c152e117`, matching the
  freshly built v20 context. That context has caps disabled and **78 of 93 held
  SKUs still at targets of one or two**, with 21 SKUs having repeat profitable
  own-payment evidence. This supports the mechanism found in the frozen replay;
  it is not a claim of stable future stock or causal earnings improvement.
- The supply window remained closed during verification. No larger live supply
  purchase is claimed. The forecast comparison and boundary tests establish the
  code behavior; the next natural windows provide realized acquisition evidence.
