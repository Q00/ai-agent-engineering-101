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

Deployment and final live verification are recorded below after completion.

## Sources for model assignment and pricing

- https://developers.openai.com/api/docs/guides/model-selection
- https://developers.openai.com/api/docs/models/gpt-6.1-sol.md
- https://developers.openai.com/api/docs/models/gpt-6.1-terra.md
- https://developers.openai.com/api/docs/models/gpt-6-luna.md
