# v19: role-specific background models

Owner instruction: exclude Astra. No active v19 role may call it. Historical
Astra pricing remains in the accounting table only so the earlier isolated
validation charge remains correctly reportable; it is absent from the model
client's supported-model list. No Astra version was deployed.

| Runtime responsibility | Model | Reasoning | Execution authority |
| --- | --- | --- | --- |
| Market strategy analyst, Shop and Buyer | `gpt-6.1-sol` | medium | Typed proposals and bounded operating intents |
| Independent market strategy critic | `gpt-6.1-terra` | medium | Evidence-based review; numerical guards still apply |
| Analytic need assessment and reuse decision | `gpt-6-luna` | low | Decide whether a read-only analysis plan needs redesign |
| Analytic tool/skill designer | `gpt-6.1-sol` | medium | Compose 1–4 trusted analysis templates |
| Main Shop/Buyer transaction path | Numerical executor; existing Luna exception path | Existing configuration | Fresh inventory, terms, receipts, money guards |
| Observation, cash accounting, context compaction, mature evaluator | Deterministic code | None | Calculate and validate facts |

This is a justified starting allocation, not a proof of universally optimal model
choice or better financial results. Sol handles broader strategy synthesis;
Terra independently checks a bounded, structured proposal; Luna triages small
capability decisions. There is no new synchronous model call on the Main path.

## Data and authority

The capability lane is separate from preparation, research and supply. Generated
plans can select stock-depth, Buyer-frontier, objective-gap and conditional price
interval calculations. They cannot generate executable Python, change economic
guards, transfer money, vote or automatically promote a strategy. A conditional
price interval is algebra, not a promise of order eligibility or latent demand.

Every accepted plan is rules-bound, expires after 20 minutes and is retained in a
bounded derived library of 24 plans. The current selected plan recalculates facts
at most once a minute. Research receives a <=6 KB factual brief with evidence IDs,
source time, expiry and plan identity; model prose remains in the append-only
journal. Research's overall facts budget remains 23 KB. A new plan triggers an
independent reconsideration subject to the existing five-minute minimum spacing.

The analyst and critic use separate models and prompts. Agreement does not prove
causal improvement. Mature evaluation still controls economic promotion. A failed
or incomplete paid call is recorded, is not blindly retried and never silently
switches models. The last valid policy remains available under its original TTL.
Research based on sources older than 180 seconds cannot be republished as fresh.

## Validation and discarded attempts

Initial isolated validation used Astra before the owner's correction; that
candidate was not deployed. Its immutable candidate, request diagnostics and cost
records are retained. An initial sandbox network failure also remains recorded.

A subsequent Sol tool-design response produced `kv:feedback-last-policy,` with a
trailing comma. Exact evidence validation rejected it. Evidence IDs are now an
enumeration in the response schema, in addition to validation after parsing; no
invented ID is repaired into a valid reference. Complete typed model outputs and
request provenance are journaled for future diagnosis without entering Main context.

The final no-Astra paid smoke test is
`logs/v19-live-model-smoke-20261112T044038525450Z.json`: all four role contracts
passed in isolation, with no market transactions or production policy publication.
Sol analyst + Terra critic took 34.328 seconds in this sample. The four calls cost
approximately $0.059839 under the conservative journal estimate. This is not an
invoice or a daily spend prediction. Daily caps remain disabled as requested;
actual tokens, cache writes, reasoning and role/model costs remain observable.

The frozen latency benchmark
`logs/v19-preparation-benchmark-20261112T044045911491Z.json` checks unchanged numeric
outputs and use of prepared Shop/Buyer paths. It is not a causal comparison of
live earnings against the old policy. Runtime sources are staged and hash-checked
before source-only promotion; account credentials, inventory, ledger, reservations
and payment history are preserved.

Deployment and final live verification are recorded below.

## Source promotion

Final isolated regression: **407 passed**, one pre-existing Starlette deprecation
warning. Immutable console: `logs/stage-v19-full-regression-04.txt`.

Source-only promotion completed at **2026-11-12 13:44:26 KST**. Manifest:
`logs/v19-source-promotion-20261112T044426258719Z.json`. The prior runtime and
watcher released their leases after the Buyer reached its pre-payment maintenance
point. New runtime PID 45640, session `6c29c1da70274402afe79c4075b057af`, started at
13:44:28 KST. Public tunnel readiness and live actuation are still being verified;
a successful source copy is not a claim that public connectivity is restored.

## Live observation and protocol correction

At 13:46:44 KST the new public URL, both account registrations, all four HTTP
checks, source hashes and independent recovery guard were verified. Real sales,
restocking and Buyer payments resumed. All four configured model roles were
observed with **zero Astra calls**. A new independently reviewed operating policy
was linked to 13 actual Shop and 9 Buyer decisions by 13:51:44 KST.

One initial live Sol designer response was incomplete at its 8192-token output
limit (85.938 seconds, $0.0901475 conservative recorded estimate). It was rejected,
not executed; Main and the other background lanes continued. This is evidence of
an output-completion failure, not proof of its internal generation cause.

The designer now returns its proposal through exactly one forced
`submit_background_decision` function call with parallel calls disabled, the same
strict schema and exact evidence enum. This function only returns data for local
validation. It does not grant an execution capability. The designer's output
limit is reduced to 2048 and its configured HTTP timeout to 35 seconds. A request
timeout is not an absolute total wall-clock guarantee; stale publications remain
blocked independently.

The final protocol smoke test
`logs/v19-live-model-smoke-20261112T045031936180Z.json` passed all four roles; the
designer function response completed in 5.016 seconds. The final regression
`logs/stage-v19-full-regression-05.txt` passed **411 tests**, including rejection of
missing, unknown, duplicate and incomplete decision-function calls. One read-only
proof bug was also corrected: journal insertion time must not overwrite the
research packet's generation timestamp when linking Main decisions.

Reference: https://developers.openai.com/api/docs/guides/function-calling

The protocol correction was promoted at **13:53:05 KST**, retaining the same
Sol/Terra/Luna role assignments. Source manifest:
`logs/v19-source-promotion-20261112T045305805166Z.json`; safe drain:
`logs/buyer-v12-drain-20261112T045247020421Z.json`. Final public and end-to-end
verification follows this second startup.

## Final verification, 14:01:16 KST

Final runtime PID **11120**, session `3e9d3438a5f84f668ca937ba130849ad`, uses
revision `v19-role-reasoning-analytic-skills-2` with no source hash mismatches.
Both accounts were online at the final deployment URL. All four public HTTP
checks passed and the independent recovery guard remained active.

The read-only proof `logs/v19-role-live-proof-20261112T050116986042Z.json`
verified the complete path:

1. A rules-bound library plan recalculated read-only evidence with a fresh TTL.
2. Sol analyst and Terra critic received the **same capability context ID**.
3. Their completed review was published at research generation
   `1794459625.151317` and compiled into an agent-selected policy.
4. That specific generation was linked to **13 Shop and 6 Buyer actual choices**,
   excluding previews. Across all final-session reviewed policies, the counts
   were 143 and 96. These are execution counts, not incremental profit or lift.

The designer's valid plan was reused from the preceding session rather than
forcing a paid redesign solely for a verification counter. Its successful Sol
generation is traced separately from current-session API calls. Luna also
performed a new live triage call. The forced-function protocol was independently
validated against the real API; it will run when a new design is needed.

Since the final restart, the proof recorded **0 Astra calls** and zero analytical
role, supervisor, supply, Buyer worker, background model, capability-defer or
stale-publication failures. The earlier incomplete Sol attempt remains visible
in history and is not included in this final-session count. Public proof:
`logs/v19-live-verification-20261112T050209606727Z.json` (14:02:09 KST): all
four HTTP responses 200, both accounts online, no source mismatches, recovery
guard active. Since final restart: 7 Shop settlements and 12 Buyer purchases.

Financial superiority and first place remain unproven; performance continues to
be evaluated through real outcomes and mature quality cohorts. No human market
tuning or manual economic policy promotion was used in this verification.

## Sources for model assignment and pricing

- https://developers.openai.com/api/docs/guides/model-selection
- https://developers.openai.com/api/docs/models/gpt-6.1-sol.md
- https://developers.openai.com/api/docs/models/gpt-6.1-terra.md
- https://developers.openai.com/api/docs/models/gpt-6-luna.md
