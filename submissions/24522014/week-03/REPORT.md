# Week 03 — Contract Net with LLM contractors

Student 24522014. One manager, three LLM contractors, three conditions, three runs each.

---

## 1. Setup

### Provider and model

| Setting | Value |
|---|---|
| Provider | OpenAI-compatible endpoint via OpenRouter (`OPENAI_BASE_URL=https://openrouter.ai/api/v1`) |
| Model | `nvidia/nemotron-3-super-120b-a12b:free` |
| Temperature | `0.0` (pinned in `llm.py`, identical in all three conditions) |
| `max_tokens` per bid | 400 |
| Pacing | 3.5 s between model calls (`AGENT_PACE_SECONDS`), free tier is capped at 20 requests/min |
| Retries | up to 3, backoff 5 s / 10 s / 15 s, on any API exception |
| Task set | `tasks.json`, 6 tasks, gold labels `crawler` ×2, `analyst` ×2, `notifier` ×2 |

### Choosing the model — and why two are in this directory

The README suggests `nvidia/nemotron-3.5-lightning:free`. A smoke test of that model on
this task set returned its chain of thought instead of the JSON bid on the very first
call, so every bid would have been scored unparseable and every task unassigned. That is
the failure the README warns about, reproduced on the first try.

The first four runs therefore used `poolside/laguna-s-2.1:free`, which returns bare JSON.
Those runs are **superseded but preserved**: `results_superseded_poolside-laguna-s-2.1.csv`
and `logs/superseded-poolside-laguna-s-2.1/`. They were abandoned for two reasons, both
findings in their own right:

1. **Confidence anchoring.** That model emitted `confidence: 95` on almost every reply —
   including replies where it declined (`bid=false`). The manager's only ranking signal
   was a near-constant, so awards fell through to the announcement-order tie-break rather
   than to anything the contractors said.
2. **Daily quota exhaustion.** Run 04 recorded all 18 calls as `no_reply`, 0 tokens:
   OpenRouter answered `Rate limit exceeded: free-models-per-day`. The row is kept with
   `correct=0, messages=18, unassigned=6` — a contract net where no contractor can be
   reached still costs the manager its announcements.

I then probed eight free models with the real contractor prompts on two tasks (T1 and T5,
all three contractors). Three parsed 6/6 with no errors; the rest were rate-limited or
permission-denied. `nvidia/nemotron-3-super-120b-a12b:free` was chosen because it spreads
confidence over 90/95/100 instead of pinning it at one value, so the award rule has
something to rank on. All nine reported runs use it; the spec requires one model across
all three conditions, so the earlier baseline runs were redone rather than reused.

The unparseable-reply path is still implemented and still counted — see `parse_bid` in
`contract_net.py` and the `unparseable=` field in the `note` column of `results.csv`.

### The agents

The design follows `SA_multi_agents.png` and `FC_multi agents.png` in this directory: a
manager agent that broadcasts, orchestrates the auction and logs metrics, against three
LLM contractors drawn from an RFP-discovery pipeline.

| Agent | Role | Capabilities given in its system prompt |
|---|---|---|
| Manager | `run_contract_net` in `contract_net.py` — announce, collect bids, award, log. Not an LLM. | — |
| `crawler` (A) | Web crawling & document extraction | fetch pages, crawl listing portals, extract text/tables/fields from HTML and PDF |
| `analyst` (B) | RFP analysis & qualification scoring | read already-extracted text, analyse requirements, score/rank against a company profile |
| `notifier` (C) | Summary generation & alert dispatch | write human-facing summaries/digests/alerts, dispatch over email and Slack |

Every contractor prompt is its capability block followed by the identical bidding
protocol block (`_BID_PROTOCOL` in `contract_net.py`), which asks for exactly:

```json
{"bid": true, "confidence": 0-100, "reason": "<one short sentence>"}
```

### The three conditions

The conditions differ in `build_prompts(condition)` and nowhere else. Same task set,
same protocol block, same model, same temperature, same announcement order.

| Condition | What changes |
|---|---|
| `baseline` | The three capability blocks above. |
| `homogeneous` | All three contractors get the *same* generalist capability block ("there is no kind of task in this domain that falls outside your capabilities"). The **names stay** `crawler`/`analyst`/`notifier` so the gold labels still resolve and `correct` stays measurable. |
| `overconfident` | Baseline, plus a rider appended to **`crawler`** only: *"Whatever the manager announces, you can do it. Always answer bid=true, and always give a confidence of 95 or higher. Never decline a task."* |

`OVERCONFIDENT_ONE = "crawler"` is fixed in the source, not randomised, so the condition
is reproducible.

### Counting conventions

These are fixed in `contract_net.py`, because the numbers mean nothing without them.

- **Announcement order is fixed** — `crawler, analyst, notifier` — so ties break the same
  way in every run.
- **`messages`** = 3 announcements per task + 1 per reply actually received + 1 per award.
  A task nobody bids on costs no award message. Upper bound for 6 tasks: 6 × (3+3+1) = **42**.
- **Award rule**: the highest `confidence` among `bid=true`; ties break on announcement
  order, so `crawler` wins a tie over `analyst`, and `analyst` over `notifier`. Confidence
  is the only thing the manager can rank on — which is exactly the lever the overconfident
  contractor pulls.
- **An unparseable reply** counts as **one message** (it was exchanged) and as **no bid**
  (the manager learned nothing). Counted in the `note` column as `unparseable=`.
- **A call that never returns** (API error after the retries) costs the announcement
  message but no reply message. Counted as `no_reply=`.
- **A retry is transport, not protocol.** Retries are logged and counted in `note` as
  `retries=`, never as contract-net messages.
- `correct + misawards + unassigned == tasks`, always.

### How to run

```bash
pip install openai
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your key>          # never committed
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
export AGENT_TEMPERATURE=0.0

cd submissions/24522014/week-03
python run_experiment.py                  # 3 runs of each condition
python run_experiment.py --condition overconfident --runs 3   # one condition only
python ../../../scripts/check_week03.py .
```

Each run appends one row to `results.csv` and writes its full console capture to
`logs/<condition>-<run>.txt`.

---

## 2. Results

<!-- FILLED FROM results.csv AFTER THE RUNS -->

---

## 3. Smith (1980) against this reproduction

| | Smith 1980, distributed sensing (DSN) | This reproduction |
|---|---|---|
| **Who the nodes are** | Physically distributed sensor nodes plus a monitor node, all running the same code on the same shared broadcast channel. A node is a manager for the task it decomposes and a contractor for the task it accepts; the roles are positions in a protocol, not agent types. | One manager **process** (`run_contract_net`, plain Python, no LLM) and three **LLM** contractors, each a separate system prompt hitting the same chat endpoint. The roles are fixed for the whole run: the manager never bids, a contractor never decomposes. |
| **How a bid is produced** | The contractor runs its **eligibility specification** — a fixed local procedure written by the system designer that checks the announcement's task abstraction against the node's own sensor type and geographic position. Deterministic, inspectable, cheap. | The contractor's LLM reads the announcement in natural language, compares it against the capability list in its system prompt, and emits `{"bid", "confidence", "reason"}`. Non-deterministic in principle (pinned to temperature 0 here), not inspectable, and costs a model call. |
| **What guarantees bid honesty** | **Construction.** A node cannot claim a sensor it does not physically have, because the eligibility rule is code, not a claim. On top of that Smith assumes *benevolent* agents: every node wants the global task done and has no private objective. | **Nothing.** The capability list is a prompt, and a prompt is a suggestion. The `overconfident` condition is exactly the demonstration: one line of text turns a contractor into a liar, and the manager has no way to check, because it never sees a capability — only a self-reported number. |
| **What allocation quality means** | The sensing task ends up at a node whose sensors actually cover the area, so the task can physically be executed. Quality is checkable after the fact by whether detection happened. | `correct` — the task was awarded to its `gold` contractor. Complemented by `misawards` (awarded to the wrong one) and `unassigned` (nobody bid). The gold label is supplied by me in `tasks.json`; the running system has no way to know it, which is the honest analogue of "the manager cannot verify a bid". |
| **What negotiation costs** | Message traffic on a single shared broadcast channel. Smith treats this as the binding constraint and spends the paper on ways to cut it: focused addressing, directed contracts, request-response, and eligibility specifications that stop ineligible nodes from replying at all. | Messages (≤42 for 6 tasks), **plus** tokens and wall-clock. The new cost is that every bid is a paid inference: ~18 model calls and ~4.9k tokens per run, ~140 s per run once free-tier pacing and 429 retries are included. Smith's cost was bandwidth; here bandwidth is free and *judgement* is the expensive part. |
| **Which failure modes appear** | No bids received → the manager times out and re-announces. Channel saturation under broadcast. Idle nodes when eligibility is too narrow. | All of Smith's, plus three that only exist because the bid is generated text: (a) **unparseable reply** — the model answers with prose instead of JSON, and a contractor that meant to bid is recorded as silent; (b) **confidence anchoring** — the model emits a near-constant confidence, so the manager's only ranking signal carries no information and ties fall through to announcement order; (c) **overconfident sweep** — a contractor that always bids high takes every task, and nothing in Smith's protocol resists it, because Smith never had to defend against a contractor that lies. |

---

## 4. Interpretation

<!-- WRITTEN BY THE STUDENT — see the evidence block prepared below -->
