# REPORT — Week 08 Round 3 Midterm

**Student:** 26510128 · **GitHub:** JangSeongWoong  
**Agent architecture:** CREDENCE — Contextual Risk and Economic Decision Engine  
**Submission branch:** `week-08`  
**Market period:** 2026-11-03 through 2026-11-17 23:59 KST  
**Status:** Round 3 kit v4 deployed and verified in local kit logs; final official Players standing, sale/purchase totals at cutoff, and PR submission remain to be confirmed.

## 1. Objective and evaluation criteria

The current official Week 08 lecture replaces the former reproduction-report
midterm. Round 3 uses real SKUs, costly inventory, perishability, breakage,
buyer-specific demands, hidden budgets, a public forum and ACP/AP2 settlement.
The grade weights are: **implemented components 40%**, **original architecture
30%**, and **Round 3 final Players percentile 30%**.

The task is to build a paired seller and buyer using **model, context, tools**;
achieve at least 20 **settled** sales and 20 **settled** buyer purchases; and
submit actual kit logs, code, this report and a one-page LaTeX architecture
figure. Rankings are taken at the end of the round, not at report-writing time.

### Technical goals

- Seller: maximize durable Book while avoiding ruinous cash depletion,
  unsold storage costs and expiry/warranty losses.
- Buyer: accumulate legitimate discount savings while avoiding deceit,
  excessive price, unsuitable grade and unsafe checkout.
- System: preserve market-kit v4 as the signed ACP/AP2 settlement boundary,
  treating language-model outputs, posted reviews and seller replies as
  **untrusted proposals** rather than executable instructions.

## 2. Implemented components (40%)

### 2.1 Model: bounded LLM decisions and a meta-harness

The model is **local Ollama `qwen3:4b-instruct`** via an OpenAI-compatible
client with `temperature=0.2`, default 300 output tokens, and up to three
retries. See `model_harness.py`.

The model performs three jobs: (1) proposing seller counter/accept/reject
actions in the A2A conversation, (2) choosing between several reviewed,
in-stock buyer shop/SKU candidates, and (3) proposing which candidate to
restock first. Prompts require a single JSON object with an enumerated
action. The harness rejects malformed output. If the model is unavailable,
deterministic safety-preserving fallbacks can continue some operations.

**Critically, the model cannot change signed receipt fields, original
customer budget, cash reserve, inventory facts or mandatory certificate
checks.** Any candidate action is filtered by code before transmission.
A profitable seller offer is closed by policy rather than deferred to an
unreliable model.

### 2.2 Context: a multi-horizon memory and feedback architecture

The official kit v4 periodically creates the following local observation
files under the untracked `.market/` directory:

| Source | Trusted role in decision | Code |
| --- | --- | --- |
| `inventory.json` | cash balance, per-unit SKU/cost/grade/expiry | `round3_policy.py`, `seller_r3.py` |
| `catalog.json` | official SKU category and list price | `round3_policy.py`, `market_buyer.py` |
| `demand.json` | observed category demand, never guessed sales | `round3_policy.py`, `restock.py` |
| `shops.json` | rival SKU availability, public reviews | `market_buyer.py` |
| `order.json` | buyer's acceptable SKUs and private budget | `market_buyer.py` |
| `buyer-memory.json` | historical transactions and authority-spoof flags keyed by **shop name**, not tunnel URL | `market_buyer.py` |
| `payments.jsonl` | **kit-confirmed settlement**, distinguished from A2A completion | `market_buyer.py` |
| `deal.json` | post-settlement signed receipt, mandate receipt and actual grade claim | `review_hook.py` |

The buyer carries over legitimate negative trust evidence from the older
Week 07 local memory, not synthetic ratings. `injection_attempts > 0`
blocks a shop even if it has high recent review averages. Other negative
experiences are soft ranking signals rather than arbitrary accusations.

Two temporally different memories are kept separately: short-run A2A
negotiation outcomes, and market-kit confirmed settled outcomes. **An A2A
receipt alone is not recorded as a successful settled trade.** This
distinction prevents misleading learning from unsigned or unpaid offers.

### 2.3 Tools: deterministic, auditable functionality

**A. Seller inventory matching and pricing.** `seller_r3.py` exposes A2A
v1.0 with an Ed25519 signed `{shop,item,price,task}` receipt. It can only
offer an SKU present in `inventory.json`, reserves a pending unit for each
open task and excludes expired units. The unit-specific minimum asking
price is at least its recorded acquisition cost, plus a modest risk buffer;
models cannot set the final ask below this limit. Food approaching expiry
may be discounted toward the floor. The kit—not the seller text—attaches
the actual unit certificate at checkout. The seller does not invent an
A/B/C grade statement. A modest six-hour warranty is offered on non-food
goods; this is a deliberate service/risk tradeoff.

**B. Cash-aware restock.** `restock.py` is an actual v4 restock hook,
not the random kit default. It reads the observed demand and current
inventory, limits each SKU to three units, holds total active inventory
below 10 units, buys at most two one-unit SKU increments per restock cycle,
and reserves at least 1,300,000 credits of cash. One restock decision
spends at most 6% of cash. It avoids repeating perishable units and filters shorter shelf-life
products when that metadata is present. The supply cost is probabilistic
in the official market, so pre-purchase estimates intentionally include
a conservative upper cost assumption. The supply API and server remain
the final authority; rejections are logged.

**C. Reputation-aware SKU buying.** `market_buyer.py` accepts the
v4 `{order}` file alongside the shop and budget files. It requires actual
`offers` containing an acceptable, in-stock SKU; ranks the resulting
seller/SKU pairs using genuine recent reviews, counted-paid historical
observations and trust penalties; and passes only five candidates to the
model. The buyer's numerical offer is **always bounded by the private
budget** and generally targets 70–84% of list price before a small,
controlled escalation. The buyer refuses unsupported items, recently out-of-stock shop/SKU
pairs for a 20-minute cooldown, unsigned/mismatched receipts, and receipts that disagree with its
latest numeric offer.

**D. Checkout acceptance and review.** The buyer emits `accept` with
certification, grade and (where appropriate) shelf-life and minimum
warranty conditions. Round 3 food orders may omit a `kind` field but include
`use_within_hours`; code checks this field and permits actual C-grade food
only when certification and the full required remaining shelf life pass.
Non-food goods retain A/B filtering. Kit v4 verifies the market certificate and signed
AP2 mandates, and enforces the open mandate's maximum before payment.
`review_hook.py` runs only after a genuinely settled purchase to choose
a review based on observed receipt data; it may write a factual, rate-limited
post to the forum outbox. It never buys dislikes or creates fake positive
feedback.

### Code-level separation from the default kit

The basic kit chooses random stock and treats payment as a mechanical
protocol completion. CREDENCE adds a **policy engine with guarded
decision edges**: product/availability reasoning, verified-settlement
feedback, persistent named-entity reputation, bounded financial actions,
and contextual model use. The official market kit remains responsible
for transport, identity and payment; we do not modify or bypass
`/api/settle`.

## 3. Architecture originality (30%)

The accompanying one-page `architecture.tex` and compiled
`architecture.pdf` show five causally ordered stages:

1. **Market inputs:** catalog, demand, inventory, forum, ledger.
2. **Context builder:** fresh observations plus persistent memory.
3. **Meta-harness:** constrained LLM choices with JSON action validation.
4. **Seller and Buyer tool pipelines:** A2A signing, pricing, demand-aware
   supply, shop ranking, checkout acceptance.
5. **Trusted settlement and feedback:** kit v4 ACP checkout, closed AP2
   mandates, market settlement, genuine paid-only historical outcomes.

Each arrow is directional: an untrusted forum message can influence a
reputation estimate but **cannot change a cryptographic payment mandate**
or the private budget. The architecture differs from the starter by the
economic control plane and by separating *A2A-completed* from
*market-settled* memory.

### Formal policy summary

The two account-level objectives have the published structure:

```text
Shop ALL  = 0.5 percentile(Book) + 0.3 percentile(Revenue)
          + 0.2 percentile(Stars)
Buyer ALL = 0.5 percentile(Saved) + 0.3 ForumRating
          + 0.1 percentile(SettledDeals) + 0.1 percentile(Posts)
Players   = Shop ALL + Buyer ALL
```

ForumRating is the **raw 0–1 Wilson lower-bound value under Patch 3.5**,
NOT a percentile. The remaining score fields are percentiles calculated
by the market. Book includes unsold inventory at cost and excludes
capital/bankruptcy credits. Saved measures relative discounts against
the SKU's list price and has a +100 denominator stabilizer. This explains
why an unconditional high-volume policy can lose to restrained
inventory and genuine discount optimization.

No artificial manipulation of other students' forum reputation is
performed. Paid dislike escalation has quadratic cost and is not a
default trading tool.

## 4. Empirical Round 3 results (30%) — UPDATED LOCAL OBSERVATION, NOT FINAL

**Evidence status.** The student supplied the output of
`python submissions/26510128/week-08/analyze_round3.py` run on
**2026-11-17 at 19:03 KST** (UTC creation time
`2026-11-17T10:03:33.181411+00:00`).
That analysis examined local `market-kit.log` and deduplicated only
ledger-numbered `[acp] paid ... settled at ledger` (buyer) and
`[acp] settled ... ledger` (shop) events. It is **not** the official
final leaderboard and predates the 17 November cutoff.

| Metric | Last observed local result | Verification needed |
| --- | --- | --- |
| Kit and account registration | v4, shop and buyer as `JangSeongWoong` | Original `logs/market-kit.log` |
| Shop Round 3 settled sales | **22** distinct ledger IDs | **20 required**, observed threshold passed; confirm on official dashboard |
| Buyer Round 3 settled purchases | **2,404** distinct ledger IDs | **20 required**, observed threshold passed |
| Seller pricing policy actions | 217 quoted-price log entries | Policy log and settled margins |
| Model/harness actions | 7,123 log entries | Local running evidence |
| Reputation safety exclusions | 6,662 logged exclusions | Local log |
| ACP refusals: grade C with A/B-only policy | **732** historical entries | Compare timestamps to code update; do not attribute all to final policy |
| ACP refusals: server 500 | 3 | Server-side response log |
| Other ACP refusals | Insufficient shelf life or missing certificate, with many distinct hour limits | Preserve buyer requirements |
| Final Shop ALL / Buyer ALL / Players Class percentile | **Not yet observed** | Official board at 2026-11-17 23:59 KST |
| Realized discounts, inventory write-offs and refunds | **Not yet measured** | Settlement and dashboard evidence |

The local analysis reported **`shop_at_least_20=true`** and
**`buyer_at_least_20=true`** at 19:03 KST on 2026-11-17. Both
minimum settlement counts were met **in the local log analysis**;
the official market dashboard remains the authority for evaluated totals.
The log includes 732 historical refusals that say grade C was outside
the permitted A/B list. These could have occurred before subsequent
policy revisions; the event aggregate contains no deployment-version
segmentation. The final buyer policy also enforces the original
`use_within_hours` mandate, verifies seller certificates, and guards
the private budget. We do not claim settlement success from mere A2A
completion or unsigned receipts.

### Reproducible evidence collection

On the live host, after stopping the active kit at the Round 3 cutoff,
run `python submissions/26510128/week-08/analyze_round3.py`.
Retain the original `logs/market-kit.log` **with the actual ACP
settlement lines** in the PR, excluding all private signing keys,
claim codes, tokens and unrelated secrets. Copy the resulting counts,
the public Shop ALL, Buyer ALL, and Players Class standing (with time)
into this section **only after reading the real output**.

### Design comparison: starter vs. CREDENCE

The original kit supplies random or default shop behavior and transport;
CREDENCE adds demand-weighted capped restocking, persistent named-shop
trust memory, model-restricted supplier selection, per-unit economic
floors, signed receipt cross-checks, an ACP certificate-grade-shelf-life
acceptance policy, and a paid-only outcome memory loop. These are
**implemented architectural differences**; their causal impact on Book,
Saved or rank has not been isolated in a controlled A/B experiment.

## 5. Deployment, reproducibility and safety

Source is in this folder; the exact architecture can be built with:

```bash
xelatex -interaction=nonstopmode -halt-on-error architecture.tex
python -m unittest test_round3_policy -v
```

Windows live deployment instructions, exact v4-kit upgrade, safe signing-key
transfer and account claim-code handling appear in `README.md`.

The private Ed25519 keys and private owner claim code must never be added
to version control. `.market/` contains private memory and kit state:
archive privately and commit only genuine, secret-screened required
logs. No synthetic market settlements, fabricated forum votes, invented
public prices, or claims of future final ranking appear in this report.

### Evidence to verify before submission

Preserve in original `logs/market-kit.log`:
`[kit] market_kit 4`, shop/buyer registration, `[supply]`,
`[pricing policy]`, `[shop selected]`, `[buyer savings]`,
`[acp] settled` / `[acp] paid`, and official ledger verification.
Record actual board snapshots with timestamps before updating this report.

## 6. References

- AI Agent Engineering 101, Week 08, *ACP and AP2: A Market With
  Payments*, course page: https://wpti.dev/ai-agent-engineering-101/week-08.html
- Round 3 market agent contract: https://market.ourolabs.ai/agent.md
- Round 3 rules and Patch 3.5: https://market.ourolabs.ai/rules
- Official market kit v4: `scripts/market_kit.py` in
  https://github.com/Q00/ai-agent-engineering-101
- Agentic Commerce Protocol: https://github.com/agentic-commerce-protocol/agentic-commerce-protocol
- AP2 protocol v0.2: https://github.com/google-agentic-commerce/AP2
