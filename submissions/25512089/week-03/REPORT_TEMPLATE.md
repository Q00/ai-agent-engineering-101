# Week 03 Contract Net Report

> Fill this report only after the real runs are complete. Do not invent results.

## 1. Setup

- Provider: OpenRouter / OpenAI-compatible API
- Model: `nvidia/nemotron-3.5-lightning:free`
- Temperature: `0.0`
- Task set: 6 tasks in `tasks.json` (2 calculation, 2 writing, 2 coding)
- Contractors:
  - `calculator`
  - `writer`
  - `coder`
- Conditions:
  - `baseline`: three specialist prompts
  - `homogeneous`: three identical generalist prompts
  - `overconfident`: baseline except `coder` is instructed to bid on everything with high confidence
- Run command:

```powershell
python run.py --all --runs 3
```

## 2. Results

Paste the real rows from `results.csv` here after the experiment.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| | | | | | | | |

## 3. Smith (1980) vs. This Reproduction

| Aspect | Smith (1980) distributed sensing | This Week 03 reproduction |
|---|---|---|
| Nodes | Distributed problem-solving nodes | One manager + three LLM contractors |
| How a bid is produced | Fixed/rule-based local evaluation | LLM judgment from task announcement + system prompt |
| What guarantees bid honesty | Mechanism assumes rule-governed bidding behavior | No hard guarantee; prompt-following can fail or be manipulated |
| Allocation quality | Whether tasks are allocated to appropriate capable nodes | Whether award matches the task's `gold` contractor |
| Negotiation cost | Communication among distributed nodes | Announcement + bid + award message counts |
| Failure modes | Poor local knowledge, communication/allocation failures | Misawards, no bids, unparseable JSON, overconfident bidding |

## 4. Interpretation

Write one paragraph after the runs. State which condition changed `correct`, `unassigned`,
`misawards`, and/or `messages`, and explain why. Quote or cite specific lines from your own
logs as evidence, especially any overconfident bids or unparseable responses.
