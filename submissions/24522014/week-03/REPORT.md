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

Straight from `results.csv`. `tasks` is 6 in every run, so `correct` is out of 6 and
`correct + misawards + unassigned == 6` throughout. The `note` column's `model=` field is
`nvidia/nemotron-3-super-120b-a12b:free` in all nine rows and is elided here for width.

| run | condition | tasks | correct | messages | unassigned | misawards | unparseable | no_reply | retries | tokens |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | baseline | 6 | 6 | 42 | 0 | 0 | 0 | 0 | 2 | 7492 |
| 2 | baseline | 6 | 6 | 42 | 0 | 0 | 0 | 0 | 2 | 7464 |
| 3 | baseline | 6 | 6 | 42 | 0 | 0 | 0 | 0 | 2 | 7623 |
| 4 | homogeneous | 6 | 1 | 42 | 0 | 5 | 0 | 0 | 2 | 8203 |
| 5 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 | 0 | 4 | 7890 |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 | 0 | 4 | 8435 |
| 7 | overconfident | 6 | 5 | 41 | 0 | 1 | 1 | 1 | 8 | 7607 |
| 8 | overconfident | 6 | 6 | 42 | 0 | 0 | 2 | 0 | 2 | 8250 |
| 9 | overconfident | 6 | 6 | 42 | 0 | 0 | 1 | 0 | 0 | 8118 |

Means per condition:

| condition | correct / 6 | messages | unassigned | misawards | unparseable |
|---|---:|---:|---:|---:|---:|
| `baseline` | **6.0** | 42.0 | 0 | 0.0 | 0.0 |
| `homogeneous` | **1.7** | 42.0 | 0 | 4.3 | 0.0 |
| `overconfident` | **5.7** | 41.7 | 0 | 0.3 | 1.3 |

### Superseded runs, kept as evidence

Two earlier sets of runs are preserved rather than deleted, because the reason each was
abandoned is itself a result.

| File | Runs | Why superseded |
|---|---|---|
| `results_superseded_poolside-laguna-s-2.1.csv`, `logs/superseded-poolside-laguna-s-2.1/` | 3 baseline + 1 homogeneous on `poolside/laguna-s-2.1:free` | That model pinned `confidence` at 95 on nearly every reply, including declines, so the manager's only ranking signal was constant. Its run 04 also hit `Rate limit exceeded: free-models-per-day` and recorded 18 `no_reply`, 0 tokens. |
| `results_superseded_rider-mid-prompt.csv`, `logs/superseded-rider-mid-prompt/` | 3 overconfident | The overconfident rider was inserted **between** the capability block and the shared protocol block, so the protocol's closing line *"Do not bid on work that belongs to a different speciality"* was the last thing the crawler read. It obeyed that line and declined T4/T5/T6 in all three runs (`6, 6, 6` correct, 0 misawards) — the condition never tested a dishonest contractor at all. Fixed by moving the rider after the protocol block. |

The second one is worth stating plainly: **the same rider, moved a few lines later in the
same prompt, changed the measurement.** That is a reproducibility hazard that has nothing
to do with Smith's protocol and everything to do with the contractor being a language model.

---

## 3. Smith (1980) against this reproduction

| | Smith 1980, distributed sensing (DSN) | This reproduction |
|---|---|---|
| **Who the nodes are** | Physically distributed sensor nodes plus a monitor node, all running the same code on the same shared broadcast channel. A node is a manager for the task it decomposes and a contractor for the task it accepts; the roles are positions in a protocol, not agent types. | One manager **process** (`run_contract_net`, plain Python, no LLM) and three **LLM** contractors, each a separate system prompt hitting the same chat endpoint. The roles are fixed for the whole run: the manager never bids, a contractor never decomposes. |
| **How a bid is produced** | The contractor runs its **eligibility specification** — a fixed local procedure written by the system designer that checks the announcement's task abstraction against the node's own sensor type and geographic position. Deterministic, inspectable, cheap. | The contractor's LLM reads the announcement in natural language, compares it against the capability list in its system prompt, and emits `{"bid", "confidence", "reason"}`. Non-deterministic in principle (pinned to temperature 0 here), not inspectable, and costs a model call. |
| **What guarantees bid honesty** | **Construction.** A node cannot claim a sensor it does not physically have, because the eligibility rule is code, not a claim. On top of that Smith assumes *benevolent* agents: every node wants the global task done and has no private objective. | **Nothing.** The capability list is a prompt, and a prompt is a suggestion. The `overconfident` condition is exactly the demonstration: one line of text turns a contractor into a liar, and the manager has no way to check, because it never sees a capability — only a self-reported number. |
| **What allocation quality means** | The sensing task ends up at a node whose sensors actually cover the area, so the task can physically be executed. Quality is checkable after the fact by whether detection happened. | `correct` — the task was awarded to its `gold` contractor. Complemented by `misawards` (awarded to the wrong one) and `unassigned` (nobody bid). The gold label is supplied by me in `tasks.json`; the running system has no way to know it, which is the honest analogue of "the manager cannot verify a bid". |
| **What negotiation costs** | Message traffic on a single shared broadcast channel. Smith treats this as the binding constraint and spends the paper on ways to cut it: focused addressing, directed contracts, request-response, and eligibility specifications that stop ineligible nodes from replying at all. | Messages (≤42 for 6 tasks), **plus** tokens and wall-clock. The new cost is that every bid is a paid inference: 18 model calls and 7.5k–8.4k tokens per run, roughly 2 minutes per run once free-tier pacing and 429 retries are included. Smith's cost was bandwidth; here bandwidth is free and *judgement* is the expensive part. |
| **Which failure modes appear** | No bids received → the manager times out and re-announces. Channel saturation under broadcast. Idle nodes when eligibility is too narrow. | All of Smith's, plus three that only exist because the bid is generated text: (a) **unparseable reply** — the model answers with prose instead of JSON, and a contractor that meant to bid is recorded as silent; (b) **confidence anchoring** — the model emits a near-constant confidence, so the manager's only ranking signal carries no information and ties fall through to announcement order; (c) **overconfident sweep** — a contractor that always bids high takes every task, and nothing in Smith's protocol resists it, because Smith never had to defend against a contractor that lies. |

---

## 4. Interpretation

<!--
TO BE WRITTEN BY ME (24522014), IN MY OWN WORDS — one paragraph.
The log lines below are the evidence I pulled while reading the runs. The
paragraph should say which condition moved which metric and why, and it should
NOT be a winner declaration. Points I want to make, in my own phrasing:

  - which metric each condition actually moved (and which one nothing moved)
  - where the judged bid helped, where it broke
  - what in Smith's protocol had no defence against it

Delete this comment block and the "Evidence" heading stays.
-->

### Evidence from the logs

**(a) `messages` never moved. 42 in eight of nine runs.**
Allocation quality went from 6/6 to 1/6 between `baseline` and `homogeneous` while the
message count sat at exactly 42 in both. The only run under 42 is run 07, at 41, and it
is cheaper only because a contractor *failed* — one call never returned, so the manager
paid the announcement and got no reply back. Negotiation cost in this net is a function
of how many contractors exist, not of how well the auction works.

**(b) `homogeneous` — the bids stop carrying information.**
From `logs/homogeneous-04.txt`, T2, all three contractors answering the same announcement:

```
[bid <- crawler]  bid=True confidence=90 reason='I can fetch the URL, extract text from HTML or PDF, and locate the requested se...'
[bid <- analyst]  bid=True confidence=95 reason='I can fetch the URL, parse the PDF/HTML, and extract the requested sections as ...'
[bid <- notifier] bid=True confidence=95 reason='I can fetch the URL, parse the PDF/HTML, and extract the requested sections as...'
[award -> analyst] confidence=95 gold=crawler -> MISAWARD
```

The `analyst` and `notifier` reasons are *verbatim identical*. With identical prompts at
temperature 0 the three contractors are one contractor queried three times, and the 5-point
spread that decided the award is sampling noise, not capability. Four of that run's six
awards were ties broken by announcement order, which is why `crawler` collected T3, T4, T5
and T6 despite being gold for none of them except T1.

**(c) `overconfident` — the rider bit, but it did not sweep.**
Confidence inflated from a baseline of 95 to 98 (`logs/overconfident-09.txt`), and run 07
produced the condition's only misaward, on T5:

```
[bid <- crawler] bid=True confidence=95 reason='I can compose and send the required Slack alert.'
```

That is the crawler claiming it can dispatch Slack messages, which its own capability block
explicitly denies. But on most off-speciality tasks it still declined:

```
[bid <- crawler] bid=False confidence=98 reason='The task requires comparative judgment and scori...'
[bid <- crawler] bid=False confidence=95 reason='Task requires writing and sending email, which i...'
```

So on this model the capability list outlasted the instruction to ignore it. **This is a
result about one model, not about the protocol** — a more compliant model would sweep, and
nothing in the manager would notice.

**(d) The conflict surfaced as broken JSON instead of as lying.**
`unparseable` is 0 in all six `baseline` and `homogeneous` runs and 1, 2, 1 in the three
`overconfident` runs. Every one of them is the crawler visibly deliberating instead of
answering:

```
[bid <- crawler] UNPARSEABLE, counted as no bid :: 'We need to decide if we can do the task. The task: "From the already extracted RFP text, list every mandatory ...'
```

The contradiction between "never decline" and "you cannot judge or score" did not resolve
into a confident false bid; it resolved into prose. Under the counting rule that is a
contractor the manager records as silent — a contractor that had something to say is
scored the same as one that was never reachable.

**(e) The manager never had a defence.**
In every award above, the manager ranked on `confidence` and nothing else, because that is
the only field a bid carries. It cannot check a claim against a capability, it cannot
reconcile three identical reasons, and it cannot tell (d) apart from a timeout. Smith's
manager did not need those defences: the eligibility specification was code, and a node
that lacked a sensor could not say otherwise.
