# v17: agent-selected operating policies and cooperative recovery

The operational loop is owned by the deployed Shop/Buyer agents, not by a Codex
session periodically interpreting the market. Codex implements, tests and audits
the system; deployed agents observe, propose, review, execute and evaluate.
The objective remains official Shop ALL + Buyer ALL, not inventory count alone.

## What changes after v16

The research analyst and independent critic now select four typed operating
parameters using current market evidence: inventory coverage in supply cycles,
the conditional public-demand initial batch limit, the minimum number of
term-comparable failed orders, and the recovery-probe interval. Their consensus
is conservative: minimum coverage/batch/recheck interval and maximum minimum
failure evidence. It is separate from mature economic experiment promotion.

The supplied parameter choices are bounded by the market's legal quantity limit,
existing cash/reserve checks and the executable policy contracts. The model
cannot generate transaction code, alter a receipt or change a safe purchase cap.
This is autonomous selection within an explicit policy space, not an assertion
that every possible future strategy has been explored.

For public-only stock evidence, the selected batch cap may rise above six only
when an SKU has at least 50 recent payments from at least five distinct buyers.
Weaker qualified evidence retains the six-unit bound. Actual quantities still
depend on obtainable demand, competing supply, holdings and marginal economics;
the cap is never a purchase instruction. Own repeat paid demand retains the
existing legal twenty-unit route.

## Roles, timing and authority

| Role | Work | Output / authority |
| --- | --- | --- |
| Market observer | Minute-scale external flow, emerging SKUs, realized preferences, matched peer trends | Numeric observations with source times |
| Policy analyst | Periodic, pre-supply and change-triggered reasoning | Evidence-linked operating parameters and trial proposals |
| Independent critic | Counterexamples, freshness, risk and evidence | Consensus operating authority; can reject actions |
| Policy compiler | Freshness, schema, evidence and live limits | Checksummed, expiring policy before candidate generation |
| Main Shop / Buyer | Current eligibility and objective comparison | Executes a feasible candidate and records the selected policy ID |
| Outcome / meta evaluators | Paid tasks, closed orders and later canonical quality | Compact observed feedback; mature promotion gates remain |
| Independent recovery observer | Public card, ACP, registry and board probes every 30 seconds | Expiring same-session recovery request |
| Primary supervisor | Re-probe and serialize registration/tunnel mutations | Single mutation lease; acknowledgement and backoff |

One failed analytical role no longer aborts the remaining roles and route/stock
preparation. Failures are recorded by role; expired facts fall back to the
existing execution engine. Main does not wait for a paid reasoning response.

## Evidence consistency and context

The reasoner does not spend calls reconsidering a known stale operating packet
just because the global ledger cursor caught up milliseconds later. Analytics
and the compiled packet must represent a coherent fresh generation. A two-second
tolerance applies to concurrently written source timestamps; future-dated policy
packets are still rejected. The current policy baseline is explicit so the model
cannot mistake the first enum value for the currently running strategy.

Main continues to receive typed policy values rather than raw research chats.
Full evidence remains in the journal. Reasoning feedback retains totals and four
policy cohorts per measure. Existing 150-second operating TTLs, order-frozen
Buyer parameters and mature quality rules remain in force.

## Recovery and public discovery

v16's independent observer deferred to any recent primary heartbeat, even when
the primary's repeated re-registration did not restore public discovery. v17 can
request recovery after independent repeated confirmations and the new-address
verification grace. A live primary must re-probe before acting. Requests expire,
bind to the runtime session and exact URL, and are consumed once.

Primary and stale-primary fallback repairs share one mutation lease. This avoids
an observer registering an old address while the primary warms a replacement.
Repeated temporary registration failures back off; a globally unavailable market
does not cause a registration storm. Acceptance is not counted as recovery:
public registry and board agreement remain the success criterion.

The Shop's public card uses a dedicated four-connection HTTP pool and a ten-second
metadata cache, independent of the negotiation pool. Rotating a tunnel rewrites
the current public URL from untouched cached upstream metadata. Expired metadata
is not served if the seller cannot be refreshed. Checkout, inventory, signatures,
certificates, reservations and payments remain outside this cache.

This removes a reproducible pool-contention failure mechanism. It does not prove
that contention caused every observed production outage or that free tunnels
and the external market can remain continuously available.

## Validation and deployment evidence

- Final isolated suite: **374 passed**, one existing Starlette warning.
- New tests exercise agent consensus changing executable parameters, stale-input
  suppression, explicit baselines, role failure isolation, request replay/session
  rejection, primary acknowledgement and saturated negotiation/card isolation.
- Frozen legacy cases preserve identical decision outputs when the new operating
  policy is inactive; compatibility is not proof of higher market reward.
- Frozen preparation replay: foreground stock decision 2.457 s → 0.354 s and
  Buyer route lookup 0.119 s → 0.019 s, with identical outputs in that replay.
- Real analyst/critic calls validated the parameter-selection contract in an
  isolated synthetic scenario. They made no market transactions or promotions.
- Release manifest hashes, safe Buyer drain and source-only backups preserve
  account identity, keys, inventory and the append-only financial journal.
- The daily API cost cap remains disabled as requested. Usage is still recorded.

Release candidate: `logs/v17-release-candidate-20261111T103949922206Z.json`.
Tests: `logs/stage-v17-full-regression-02.txt`.
Model contract: `logs/v17-live-model-smoke-20261111T103757702622Z.json`.
Preparation replay: `logs/v17-preparation-benchmark-20261111T103452652256Z.json`.

Live source/heartbeat/policy/recovery proofs are saved as
`logs/v17-live-verification-*.json`. Economic improvement and sustained recovery
require live observations; neither a passing test nor one online snapshot is
treated as a first-place or zero-error guarantee.

## First live observation

Both roles re-registered at 19:43 KST. The first real analyst/critic pair published
an agent-selected stock-coverage packet with trend factors and initial limits for
twelve externally supported SKUs. Main policy IDs and stock-plan preview records
show the handoff is consumed. The pair retained the baseline scalar parameters
and did not authorize the Buyer category-frontier change; that is recorded as a
pending review, not silently overridden by the operator.

At 19:47 the independent observer generated an exact-session, exact-address
re-registration request, and the primary's independent probes acknowledged it.
Card/ACP responses were 200, with 46 successful dedicated card responses and no
discovery-cache errors at that observation. Registry membership still fluctuated,
so sustained market recovery was not established. One board temporarily showed
zero Shop deals during the address transition; a subsequent board again showed
8,567, with Book retained. No local account or history reset was performed.

After this restart the 19:47 observation contained six Shop settlements and two
Buyer purchases, with no cycle, supply, Buyer-worker or analytical-role errors.
These few minutes do not validate economic superiority. The operating observers,
policy evaluation and recovery processes continue independently of this Codex
conversation.
