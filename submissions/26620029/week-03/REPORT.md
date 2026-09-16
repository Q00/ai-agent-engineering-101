# Week 03 — Contract Net with LLM Contractors

## 1. Setup

- **Provider / model / temperature**: picked at runtime by `model.py` from
  the environment — `ANTHROPIC_API_KEY` selects the Anthropic SDK, otherwise
  the OpenAI-compatible SDK is used with `OPENAI_API_KEY` (and optionally
  `OPENAI_BASE_URL`, e.g. `https://openrouter.ai/api/v1` for OpenRouter).
  `AGENT_MODEL` overrides the model, `AGENT_TEMPERATURE` overrides the
  temperature (default `0.7`). **TODO: fill in the exact model name and
  temperature actually used once runs are captured.**
- **Contractors**: three fixed identities, `alex` (coder), `brooke`
  (writer), `casey` (researcher). Only their system prompt changes per
  condition — `contract_net.py`'s `BASELINE_PROFILES`,
  `HOMOGENEOUS_PROFILES`, `OVERCONFIDENT_PROFILES`.
- **Protocol**: the manager (`run_task` in `contract_net.py`) announces
  each task to all three contractors, collects one JSON bid per contractor
  (`{"bid": bool, "confidence": 0-100, "reason": str}`), and awards to the
  highest-confidence `true` bid (ties broken by announcement order:
  alex, brooke, casey). No bid `true` -> the task is unassigned.
- **How to run**:
  ```bash
  export OPENAI_BASE_URL=https://openrouter.ai/api/v1
  export OPENAI_API_KEY=<your key>
  export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
  cd submissions/26620029/week-03
  python run_experiments.py --runs 3
  ```
  This overwrites `results.csv` and `logs/` with one row and one log file
  per run (3 conditions x `--runs`).

## 2. Results

**TODO: pending real runs.** `python run_experiments.py --runs 3` has not
been executed yet in this environment — no `ANTHROPIC_API_KEY` /
`OPENAI_API_KEY` is set here, and neither the `anthropic` nor `openai`
package is installed. The runner and crash path were verified in a scratch
copy (missing-package crash produced a correct blank-counts row with the
error in `note`), but that is not a real experiment and is not included
here. Once `results.csv` exists, paste its table here.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| _pending_ | | | | | | | |

## 3. Smith (1980) vs. this reproduction

| Aspect | Smith 1980 (distributed sensing) | This reproduction |
|---|---|---|
| Nodes | Sensor/processing nodes on a network, each with fixed local capability | One manager process, three named LLM contractors (`alex`, `brooke`, `casey`) |
| How a bid is produced | A fixed local rule evaluates the announcement against the node's known capability and load | An LLM reads the task announcement and its own system-prompt persona, then judges fit and emits a JSON bid with a self-reported confidence |
| What guarantees bid honesty | The rule is hard-coded; a node cannot misrepresent its own capability | Nothing — the `overconfident` condition exists specifically because a system prompt can tell a contractor to claim confidence it doesn't have |
| What allocation quality means | The task reaches the node best equipped (by known, fixed capability) to run it | The task reaches whichever contractor's *self-report* wins the highest confidence among `true` bids; measured here against a pre-labelled gold contractor per task |
| What negotiation costs | Bandwidth/time for announce + bid + award messages between nodes | One model call per contractor per task (announcement + bid = 2 messages), plus one award message; `messages` in `results.csv` totals these per run |
| Failure modes | Node overload, message loss, stale bids | Unparseable JSON reply (counted as a non-bid), no contractor bidding `true` (unassigned), and a contractor whose prompt bids confidently outside its real skill (misaward) |

## 4. Interpretation

**TODO: pending real runs.** This section needs log lines as evidence
(e.g. an `overconfident` run where `alex` sweeps awards it has no real fit
for, or a `homogeneous` run where `correct` drops toward chance because no
contractor's name signals a matching skill anymore) and cannot be written
honestly before `run_experiments.py` actually executes against a live
model. Fill in once `logs/` and `results.csv` hold real runs.
