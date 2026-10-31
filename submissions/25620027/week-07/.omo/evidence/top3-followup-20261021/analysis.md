# Top-three seller comparison — 2026-10-21 (Codex)

Public GET observations and current local source/log inspection; no new competitor
purchase or public review was submitted for this analysis. Raw board/registry
and cards are preserved in `market-snapshot.json` and `full-cards.json`.

## Observed board at 08:53:57 KST, Round 2

|Rank / seller|Deals|Visits|Deals ÷ visits|Stars|Reviews|Revenue|
|---|---:|---:|---:|---:|---:|---:|
|1 lee-sungsoo|3349|4320|77.52%|4.46|3349|575844|
|2 11im|3334|4243|78.58%|4.49|3334|628941|
|3 JangSeongWoong|1556|2004|77.64%|4.13|1388|283951|
|15 25620027|257|503|51.09%|4.59|257|48175|

These cumulative ratios are not controlled conversion-rate estimates. Item mix,
version, period, probe traffic, buyer selection and evaluator mix differ. LSS
has 8.59 times our visits and 13.03 times our deals. Our rating exceeds all three
at this observation. Neither poor rating nor a weaker model is established as
the primary cause. Average receipt amounts (171.95 / 188.64 / 182.49 / 187.45)
also mix items and do not establish per-item pricing or profit.

At the last existing price-monitor observation (08:44:54 KST), first asks for
lamp/chair/bookshelf were LSS 100/180/260, 11im 110/195/290, Jang 115/195/275.
Our fresh comparison yields ceilings 100/180/260, tied with LSS's observed asks.
First asks are not final sale prices or proof of identical quality.

## Strategies and limits of model evidence

- **LSS:** card claims listed-price, signed-receipt, single-reply sales and
  100/180/260 basic prices. Honest receipt matching is repeated in recent reviews.
  Some sampled reviews still describe several offers, so card copy does not prove
  measured latency or a single-message guarantee. Stamp-card and every-100th
  promotions do not establish savings below the already advertised minimum.
  Exact model is undisclosed. Public fork main SHA
  `67074f53b659253c32a0cde3a2b989f9a06b8110` and old fork SHA
  `404ec973a4e77cc074299865fd130055abdb5e18` did not expose Week07 implementation
  in inspected public branches/PRs. Absence in this scope is not proof of absence.
- **11im:** card emphasizes exact-price Ed25519 receipts and quick bargaining.
  It declares a Claude coding harness, without a model ID; Haiku/Sonnet/Opus
  cannot be inferred. Card and prior opening text include extra discount for
  promising five stars; several latest reviews criticize that condition. Do not
  copy rating-dependent discounts. Public fork main SHA
  `8a317b4dfc83910cda5305e4b4ea97f07e8e0815` did not expose Week07 source in the
  inspected public branch/PR scope. Live model and implementation remain unknown.
- **Jang:** generic catalog card; sampled reviews show high opener followed by
  lower accepted offers, with correct receipts. Public source defaults to
  `qwen3:4b-instruct` and `http://localhost:11434/v1` (Ollama), controlled by
  `AGENT_MODEL`/`OPENAI_BASE_URL`. It actually invokes a completion API and validates
  allowed actions; it is an LLM/rule hybrid. Buyer risk uses reviews and own
  failures; price policy adjusts by -5 or +3 after enough reviews/sales. These
  implementations are not established as deployed or causal to rank.
- **Ours:** fixed `gpt-4.1-nano-2025-04-14`; source and actual VPS MODEL_USAGE agree.
  Keep the model while correcting retrieval/context, then measure effects.

Primary Jang source, pinned SHA `01dce5f7fe97207281141cf2e7827ee02d37c1ca`:

- [Model defaults](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/01dce5f7fe97207281141cf2e7827ee02d37c1ca/submissions/26510128/week-07/model_harness.py#L16-L20)
- [Actual LLM and action validation](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/01dce5f7fe97207281141cf2e7827ee02d37c1ca/submissions/26510128/week-07/model_harness.py#L57-L65)
- [Buyer risk and candidate ranking](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/01dce5f7fe97207281141cf2e7827ee02d37c1ca/submissions/26510128/week-07/market_buyer.py#L32-L68)
- [Review-based price policy](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/01dce5f7fe97207281141cf2e7827ee02d37c1ca/submissions/26510128/week-07/pricing_policy.py#L68-L73)
- [Operation instructions](https://github.com/JangSeongWoong/ai-agent-engineering-101/blob/01dce5f7fe97207281141cf2e7827ee02d37c1ca/submissions/26510128/week-07/README.md#L30-L37)

## Overnight availability, not proven 24/7 uptime

The existing `../price-comparison-discovery.json` snapshot at 00:34:45 KST and
this 08:53:57 snapshot show all three alive, with increased deal counts and the
same trycloudflare hostnames. This supports activity at sampled overnight times.
It does not establish uninterrupted availability, a 24-hour guarantee, or whether
they use laptops/VPS. Registration time is not uptime. Jang's README asks users to
keep PC/Ollama/terminal on, while its REPORT deployment section still says pending;
neither establishes the current live machine/model.

## Own measured bottlenecks and candidate changes

1. Historical Mac log: 894 seller searches, 359 empty results over 60 tasks;
   the 60 model_loop_exhausted task IDs match those 60 tasks. Last empty was
   06:48:34, followed by 56 nonempty searches. Versions/inputs were mixed and
   the original requests were not logged, so do not claim all 60 are explained
   by a current defect. Structurally, old code truncated to top6 before applying
   original-request compatibility. The new synthetic seventh-candidate regression
   demonstrates and repairs that specific candidate-starvation case.
2. Prior VPS public smoke basic-lamp opening: search input861 tokens and decision
   input1883, total2744 over two model calls. Exact basic requests returned six
   candidates. Candidate1.2.1 returns only the basic SKU for a strict whole-match
   plain request, preserving search→model decision and descriptive variants.
   No measured latency or live token saving is claimed before new observation.
3. Add explicit allowed_quote_min/max hints to reduce out-of-range model retries.
   Existing price guards still reject invalid actions; code does not choose/clamp
   a price on the model's behalf.
4. Put truthful minimums, signature and numeric-offer instructions, and a dated
   comparison link in the card's first300 characters. The default kit shop list
   includes only name/URL/reviews, so card or HTML copy is not automatically seen
   before shop selection and cannot guarantee increased visits.
5. Single-message item+bid acceptance is a separate design: current code parses
   only whole numeric offers and rejects initial accept. Broad numeric extraction
   could mistake budgets/dimensions/quantity for offers. Not added in this patch.
6. HTTP requests have two log formats, not two actual requests. Count MODEL_USAGE;
   queue waiting has not been measured, so concurrency is not increased blindly.

## Extension authorization and acceptance scope

User approved **2026-10-21 21:00 KST or the existing cumulative observed5000
responses, whichever first**. Baseline4354, persistent seller log and counter floor
are retained; no automatic buyer or rejoin. Successful extension and healthy
09:00:31 count4421 are preserved in `extend-operation.log` and
`extended-runtime-status.log`. In-flight responses may log during shutdown;
this is not a provider-side hard request/billing cap. The extension is a later
authorization and does not rewrite earlier09:00 migration evidence.

Candidate checks and live rollout evidence are indexed separately. Improved
market share, profit, availability or ranking is not established by passing tests.
