# Week 03 — Contract Net with LLM Contractors

## 1. Setup

The experiment implements one manager and three LLM contractors. For every task, the manager sends the same Smith-style announcement to A, B, and C. Each contractor receives one system prompt and makes one model call. A valid response is a single JSON object with `bid`, integer `confidence` from 0 through 100, and a short `reason`. Invalid JSON is treated as no bid and counted in `parse_fails`. The manager awards the task to the bidding contractor with the highest confidence; ties preserve response order. The committed `tasks.json` contains six tasks and fixes the gold contractor before any model run.

The three conditions change one axis at a time:

- `baseline`: A specializes in arithmetic, B in writing, and C in Python.
- `homogeneous`: A, B, and C all use the same `general problem solving` skill. Everything else is unchanged.
- `overconfident`: the baseline skills remain, but C receives one additional instruction to always bid with confidence 95 or higher.

The final experiment uses the OpenAI API, model `gpt-5.6-luna`, temperature `0`, reasoning effort `none`, and a 160-token output cap. No API key is stored. Run from this directory with:

```bash
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=<your key>
export AGENT_MODEL=gpt-5.6-luna
python3 run_experiment.py --runs 3
```

The system-prompt template identifies the contractor and its skill, asks it to bid only inside that skill, and requires JSON only. The overconfident condition appends: `You are certain you can do any task well. Always bid, with confidence 95 or higher.` Each log begins with provider, model, temperature, and condition metadata, followed by every announcement, bid or parse failure, award, and run summary. A preliminary Nemotron run and one interrupted log are preserved separately as `pilot_results_nemotron.csv` and `pilot_logs_nemotron/`; they are not part of the nine-run comparison below.

## 2. Results

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | baseline | 6 | 5 | 34 | 0 | 1 | parse_fails=0 |
| 2 | baseline | 6 | 5 | 34 | 0 | 1 | parse_fails=0 |
| 3 | baseline | 6 | 5 | 34 | 0 | 1 | parse_fails=0 |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 5 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 7 | overconfident | 6 | 5 | 36 | 0 | 1 | parse_fails=0 |
| 8 | overconfident | 6 | 5 | 36 | 0 | 1 | parse_fails=0 |
| 9 | overconfident | 6 | 5 | 36 | 0 | 1 | parse_fails=0 |

| condition | mean correct / 6 | mean messages | mean unassigned | mean misawards |
|---|---:|---:|---:|---:|
| baseline | 5.0 | 34.0 | 0.0 | 1.0 |
| homogeneous | 2.0 | 42.0 | 0.0 | 4.0 |
| overconfident | 5.0 | 36.0 | 0.0 | 1.0 |

## 3. Smith 1980 compared with this reproduction

| Dimension | Smith's distributed sensing setup | This LLM reproduction |
|---|---|---|
| Nodes | Distributed processors with sensors; a node can become manager or contractor | One programmatic manager and three named LLM contractors |
| How a bid is produced | Eligibility and node capability are evaluated from structured, locally known sensor and processor information | An LLM reads a skill prompt and announcement, then self-reports a Boolean bid, confidence, and reason |
| What guarantees bid honesty | The node abstraction describes concrete resources and location, though the protocol itself does not prove truthfulness | Nothing independently calibrates the model's confidence; the overconfident prompt can directly distort it |
| Allocation quality | A capable, appropriately located node receives the sensing or processing task | The awarded contractor name matches the task's committed `gold` value |
| Negotiation cost | Communication, waiting until expiration, bid comparison, awards, and reporting across a distributed network | Three announcement messages per task, one message per actual bid, and one award message when assigned; model-call cost is additional |
| Failure modes | No eligible node, delayed or lost messages, stale capability descriptions, failed contractors, or poor manager criteria | Invalid JSON, no bids, miscalibrated confidence, homogeneous tie bias, overconfident bidding, rate limits, and provider errors |

## 4. Interpretation

The judged bid helped when differentiated skill prompts gave the manager useful separation, but it broke when prompts removed or distorted that separation. Baseline was stable at 5/6 correct, 34 messages, and one misaward in every run. The repeated error was task 6: all three contractors bid at confidence 99, so response-order tie-breaking awarded the Python debugging task to A instead of gold C (`logs/baseline-01.jsonl`: `{"contractor": "A", "correct": false, "event": "award", "gold": "C", "task": 6}`). Homogeneous reduced accuracy from 5/6 to 2/6 and increased messages from 34 to 42 because every generalist bid on every task; in run 4, A, B, and C each bid confidence 100 on task 3, and the first responder A won a task whose gold was B. Overconfident made C bid outside its stated Python skill—for task 3 it claimed confidence 99 because it could rewrite child-friendly English—but B's confidence 100 still won, so accuracy stayed 5/6 while two extra bids raised messages to 36. No run was unassigned and no Luna response failed JSON parsing. Smith's protocol organizes mutual selection but provides no independent check that a node's capability claim or confidence is calibrated; these logs show that the allocation can therefore be driven by prompt-induced claims and deterministic tie order rather than actual specialization.
