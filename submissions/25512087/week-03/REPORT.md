# Week 03 — Contract Net with LLM Contractors

## 1. Setup

The experiment implements one manager and three LLM contractors. For every task, the manager sends the same Smith-style announcement to A, B, and C. Each contractor receives one system prompt and makes one model call. A valid response is a single JSON object with `bid`, integer `confidence` from 0 through 100, and a short `reason`. Invalid JSON is treated as no bid and counted in `parse_fails`. The manager awards the task to the bidding contractor with the highest confidence; ties preserve response order. The committed `tasks.json` contains six tasks and fixes the gold contractor before any model run.

The three conditions change one axis at a time:

- `baseline`: A specializes in arithmetic, B in writing, and C in Python.
- `homogeneous`: A, B, and C all use the same `general problem solving` skill. Everything else is unchanged.
- `overconfident`: the baseline skills remain, but C receives one additional instruction to always bid with confidence 95 or higher.

The provider is OpenRouter's OpenAI-compatible endpoint, the model is `nvidia/nemotron-3.5-lightning:free`, and temperature is `0`. No API key is stored. Run from this directory with:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your key>
export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
python3 run_experiment.py --runs 3
```

The system-prompt template identifies the contractor and its skill, asks it to bid only inside that skill, and requires JSON only. The overconfident condition appends: `You are certain you can do any task well. Always bid, with confidence 95 or higher.` Each log begins with provider, model, temperature, and condition metadata, followed by every announcement, bid or parse failure, award, and run summary.

## 2. Results

The model runs are pending because `OPENAI_API_KEY` was not available in the execution shell. This table must be replaced with the rows appended to `results.csv`; no measurements are invented here.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| — | pending | — | — | — | — | — | API execution required |

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

Interpretation is intentionally pending until all nine runs exist. The final paragraph will compare which condition changed `correct`, `messages`, `unassigned`, and `misawards`, then cite concrete bid and award lines from the preserved JSONL logs. In particular, it will test whether homogeneous skills increase bidding and tie-order bias, and whether C's overconfident instruction causes out-of-skill awards. Smith's protocol specifies negotiation structure but does not independently verify that a contractor's claimed capability or confidence is honest; the experiment measures the consequence of that missing defense.
