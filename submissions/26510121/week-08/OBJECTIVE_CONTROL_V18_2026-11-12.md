# v18: objective alternatives, compact research and fast execution

The release changes the evidence and decision interface between subordinate
agents and Main. The objective remains official Shop ALL + Buyer ALL. On the
current Round 3 rules, Shop uses Book/revenue/stars with weights .5/.3/.2;
Buyer uses normalized Saved/forum/deals/posts with weights .5/.3/.1/.1. Forum
uses the raw 0..1 score; other components use rank percentiles. Absolute buyer
surplus and raw inventory quantity are not the controlling objectives.

## Diagnosis

The 11 November audit reconciles 58 pre-start stock units +40 acquired -45 sold
=53 held. The four plans requested only 42 units, chiefly one unit per lot,
despite about 560 million spendable credits. All eight then-observed research
decisions retained one-cycle coverage. See `V17_INVENTORY_DIAGNOSIS_2026-11-11.md`.

That finding has a time boundary. On 12 November before deployment, stock had
grown to 148 and v17 had subsequently selected three cycles. However, invalid
or oversized research responses could prevent renewal, allowing the reviewed
policy to expire and compiled patches to become empty. The system was capable
of changing policy but did not reliably compare or renew it.

The official rules were re-fetched on 12 November and match the 11 November
rule hash `e8e63cc2f212ac65c62062760f43c6a335f4ccfbfd78d77d258b476d5575a942`.

## Runtime roles and authority

| Role | Observes / computes | Output and timing |
|---|---|---|
| Market observer | External settlements, recent changes, competitors, observed preferences | Existing minute-scale numerical analytics |
| Objective planner | One/1.5/two/three-cycle supply alternatives under identical current data; capital and marginal Book/revenue scenarios | Background research only; no transaction authority |
| Buyer outcome observer | Closed-order conversion and active time; initial Saved; separate 6–48h confirmed quality cohorts | Compact evidence for profile/routing review |
| Information observer | Actual returned post IDs, information format, sampled later public scores | Minute-scale observation; no inferred post identities or votes |
| Analyst and independent critic | Alternatives, actual acquisition versus plans, eligibility refusals, mature results and forum evidence | Typed coverage, frontier and information-format proposals; existing periodic/pre-window/change-triggered research |
| Context curator | Duplicate policy cohorts and unnecessary examples | At most 23,000 UTF-8 fact bytes, preserving objectives, alternatives, guards and evidence IDs |
| Policy compiler | Current rules, fresh feed/board, sufficient paid evidence, market regime and reviewed intent | Existing expiring typed operating packet; no price-cap or reserve override |
| Main | Current order/negotiation, fresh executable numbers and the reviewed packet | Existing immediate numerical execution and prepared plans; no added LLM call |
| Existing mature evaluator | Completed randomized profile cohorts and actual quality outcomes | Economic promotion/rollback authority remains here |

Raw episodes and full policy evidence remain in the append-only journal. New
shadow computations intercept cache writes in memory and do not alter live
control, inventory, reservations or transaction state. A numerical alternative
is explicitly marked `execution_permitted=false`; actual supply still requires
fresh cash, availability, rules, legal quantities and refund guards.

## Behavioral corrections

### Shop

Research now sees the actual choice it can make: coverage alternatives with
units, capital, expected Book/revenue, rank-progress surrogate, upper mean-cost
stress and examples of own profitable repeat demand. Holdings, unreserved units,
short-life food, spendable cash, supply delays and acquired-versus-planned units
are included. Model scenarios are labelled as forecasts, not measured lift.

The existing analyst/critic consensus sets coverage before candidate generation;
the independent numerical allocation and live supply guards remain authoritative.
The model cannot request arbitrary stock, alter a receipt or directly transact.
Increasing an upper batch limit is not confused with setting a desired quantity.

Stock refusal now records whether matching stock is absent, expired, the wrong
grade, too short-lived or reserved. This prevents interpreting every refusal as
profitable demand for more identical units. A local pre-POST cash/control refusal
returns an immediate replan response rather than impersonating a supplier 429
and triggering up to three 30-second sleeps. Actual supplier throttles remain
unchanged and uncertain POST outcomes are never blindly retried.

### Buyer and information contribution

Short-window purchase observations retain initial normalized Saved and pending
quality separately. A separate 6–48h paid cohort supplies confirmed canonical
Saved contributions, including failures/refunds. The one-hour order window is
not falsely expected to contain six-hour goods maturity.

The analyst/critic can independently authorize `buyer_information` and agree on
balanced, same-condition price, acceptable-alternative or mature-quality focus.
These choose among existing ledger-backed drafts, never fabricate evidence.
The scheduler keeps one in five slots in the baseline mix, falls back to another
valid format when the requested draft is unavailable, preserves quotas and
deduplication, and makes no vote or reputation exchange.

Successful publication records the returned server post ID and information kind.
The observer links only exact IDs, samples later published scores, separates
posts younger than ten minutes, and exposes sparse coverage explicitly. At least
five sampled scored posts per format are required even for descriptive format
comparison; these samples do not establish causal forum lift.

### Harness and context

The old research input projected execution cohorts once but included them again
unbounded inside meta. The curator removes that duplicate and reduces optional
examples while retaining totals and authoritative references. Both analyst and
critic requests are checked independently against the existing 32 KB envelope.

Another live-call defect was joining all assistant text as a single JSON string.
The Responses API distinguishes intermediate `commentary` from `final_answer`;
the parser now selects the final message, supports a single legacy unphased
message, and rejects ambiguous, refused or incomplete outputs. This follows the
[official Responses message contract](https://developers.openai.com/api/reference/resources/responses/methods/create).
It is covered for both research and the general model chooser. Explanatory text
may be shortened; executable fields, evidence IDs and typed policy values are
never coerced or truncated to make an unsafe decision pass validation.

## Verification

- Final full regression: **399 passed** (`stage-v18-full-regression-04.txt`).
- New cases include thousands of duplicate cohorts, both request budgets,
  nonmutating coverage scenarios, actual Main handoff, sparse post identity,
  immature/forecast refunds, older quality cohorts, distinct stock refusals,
  immediate local supply refusal, phase-aware parsing and unsafe policy rejection.
- Frozen live-evidence replay: analyst 20,270 bytes; critic 21,031 bytes. A
  synthetic model decision reaches coverage/frontier/forum patches in isolation.
- Real analyst/critic calls on those facts both validate. They select three
  cycles, retain the six-unit public cap, and retain balanced information focus
  without claiming mature economic superiority or promoting an unverified trial.
- Fixed preparation replay preserves outputs: stock foreground 2.343 s → 0.382 s;
  Buyer route retrieval 0.116 s → 0.019 s. No network or model calls in this replay.
  This compares prepared versus unprepared execution on v18, not a causal market
  speed or economic comparison between releases.
- Release hashes and tested-source timestamps are checked before promotion.
  Source-only backups preserve the prior code. Keys, owner credentials, account
  state, reservations, journal history and existing orders are not replaced.

Earlier failed validation attempts are retained. The first focused run lacked
its temporary parent directory; later runs passed. Actual paid probes exposed
overlong explanation text and phase concatenation; neither failure was published
as a production strategy. Usage remains recorded with no daily cost cap.

## Release and evidence

- Release candidate: `logs/v18-release-candidate-20261112T032954215911Z.json`.
- Source promotion: `logs/v18-source-promotion-20261112T033012750194Z.json`.
- Buyer drain: `logs/buyer-v12-drain-20261112T033003989236Z.json`.
- New runtime starts 12 November 12:30:14 KST, session
  `625f5d44976543c7904351f53e1c1762`.
- Replay: `logs/v18-objective-replay-20261112T032746536024Z.json`.
- Real model contract: `logs/v18-live-model-smoke-20261112T032847248628Z.json`.
- Latency replay: `logs/v18-preparation-benchmark-20261112T032731279347Z.json`.
- Independent rollout proofs: `logs/v18-live-verification-*.json`.

At 12:33:58 KST both agents were alive and listed at the current URL, all four
public probes returned 200, tested source hashes matched, heartbeat was fresh
and the independent recovery guard held its lease. The board briefly showed
zero shop deals during registration propagation, then restored the existing
9,148-deal history without replacing any account or state.

At 12:35:39 KST, `logs/v18-actuation-proof-20261112T033539189477Z.json`
confirms a real paid analyst/critic publication, matching objective context ID,
and **16 Shop plus ten Buyer non-preview Main choices** linked to newly compiled
agent-selected policy IDs. Coverage three and conditional inventory ceiling 210
are active. No supervisor, supply, Buyer worker, objective-context, analytical
role or research-deferred errors occurred in this short post-start window.
There were three paid Shop sales and nine Buyer purchases. No v18 supply plan
had yet been consumed: the next supply opening was still in the future. Do not
describe a forecast portfolio or a quote-policy application as acquired stock.

The live context shrank from 36,054 to 22,806 UTF-8 fact bytes. A Buyer
information post was delivered with returned server ID 90342 and an exact-ID
score observation followed. The first live critic retains balanced focus;
format-specific superiority remains unproven. This verifies that the observation
and actuation paths execute, not that the global optimum has been found.

The large frozen input is preserved unchanged locally and additionally archived
as `logs/v18-frozen-evidence-20261112T032733296248Z.json.gz` for Git size limits;
`logs/v18-evidence-archive-20261112T033312052934Z.json` records both hashes.

Deployment alone does not prove higher ALL or first place. Actual policy
application, acquisition and trade results must be checked at their recorded
times; quality/refund outcomes require maturity. Tunnel registration and one
successful public probe do not establish permanent availability.
