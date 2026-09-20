# Week 03 — Contract Net with LLM contractors

> **SKELETON — not yet written.** Every `TODO` below has to be replaced with
> what the runs actually produced. Do not submit while any `TODO` remains.

## 1. Setup

- **Provider**: OpenAI-compatible (OpenRouter), `OPENAI_BASE_URL=https://openrouter.ai/api/v1`
- **Model**: TODO — the value of `AGENT_MODEL` that was actually used
- **temperature**: 0 · **max_tokens**: 512 (`contractor.py`)
- **Contractors**: A = arithmetic, B = prose, C = code (`contractor.py`, `SKILLS`)
- **Conditions**: `build_team()` in `contractor.py` is the only place the
  independent variable lives.
  - `baseline` — three different skills
  - `homogeneous` — all three set to `general problem solving`
  - `overconfident` — baseline, plus one sentence appended to C's system prompt
- **Bid format**: `{"bid": bool, "confidence": 0-100, "reason": str}`; an
  unparseable reply counts as a contractor that did not bid.
- **Award rule**: highest confidence; a tie goes to whoever answered first.
- **Messages**: one announcement per contractor + one per bid + one per award.

How to run:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>
export AGENT_MODEL=<model id>

python run.py --condition baseline      2>&1 | tee logs/baseline-1.log
python run.py --condition homogeneous   2>&1 | tee logs/homogeneous-1.log
python run.py --condition overconfident 2>&1 | tee logs/overconfident-1.log
# three runs per condition, nine in total
```

## 2. Results

TODO — paste `results.csv` here as a table, all nine or more rows, crashed
runs included.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| TODO | | | | | | | |

## 3. Smith 1980 against this reproduction

TODO — fill the right-hand column from what the runs showed, not from what was
expected beforehand.

| | Smith 1980 (distributed sensing) | This reproduction |
|---|---|---|
| Who the nodes are | sensor nodes on a simulated terrain (CNET) | TODO |
| How a bid is produced | node abstraction: position and sensor list, read off the node's own state | TODO |
| What guarantees bid honesty | TODO | TODO |
| What allocation quality means | TODO | TODO |
| What negotiation costs | TODO | TODO |
| Which failure modes appear | TODO | TODO |

## 4. Interpretation

TODO — one paragraph. Which condition moved which metric, and why. Quote lines
from `logs/` as evidence; cite the log file and the contractor by name.

Things that must be counted and stated rather than smoothed over:
- how many replies failed to parse, and under which condition
- any run where one contractor swept the awards
- any crashed run
