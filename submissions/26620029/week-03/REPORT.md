# Week 03 — Contract Net with LLM Contractors

## 1. Setup

- **Provider / model / temperature**: picked at runtime by `model.py` from
  the environment — `ANTHROPIC_API_KEY` selects the Anthropic SDK, otherwise
  the OpenAI-compatible SDK is used with `OPENAI_API_KEY` (and optionally
  `OPENAI_BASE_URL`, e.g. `https://openrouter.ai/api/v1` for OpenRouter).
  `AGENT_MODEL` overrides the model, `AGENT_TEMPERATURE` overrides the
  temperature (default `0.7`). **Runs actually captured**: `ANTHROPIC_API_KEY`
  set, provider `anthropic`, model default `claude-sonnet-4-5`, `anthropic`
  Python SDK 1.6.0. That SDK version no longer accepts a `temperature`
  argument on `messages.create()` (dropped from the Messages API since the
  Chat class this was trimmed from was written) — `model.py` was patched to
  stop passing it on the Anthropic path rather than crash every call, so the
  Anthropic runs below used the API's default sampling, not `0.7`. The
  `AGENT_TEMPERATURE` knob still applies as documented on the OpenAI-compatible
  path (e.g. OpenRouter), which is unaffected.
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

`python run_experiments.py --runs 3` against `claude-sonnet-4-5`, 6 tasks
per run:

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 42 | 0 | 0 | |
| 2 | baseline | 6 | 6 | 42 | 0 | 0 | |
| 3 | baseline | 6 | 6 | 42 | 0 | 0 | |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | |
| 5 | homogeneous | 6 | 2 | 41 | 1 | 3 | |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | |
| 7 | overconfident | 6 | 5 | 42 | 0 | 1 | |
| 8 | overconfident | 6 | 5 | 42 | 0 | 1 | |
| 9 | overconfident | 6 | 5 | 42 | 0 | 1 | |

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

`baseline` is clean across all three runs (6/6 correct, 0 misawards, 0
unassigned) — with three distinct, honestly-described skills, the highest
true-bid-confidence award reliably lands on the gold contractor. The two
manipulated conditions break this in different ways.

`homogeneous` is the worst condition (2/6 correct, 3-4 misawards per run):
once every contractor runs the same generalist prompt, the manager loses
its only real signal, contractor identity. On task-2 in `logs/homogeneous-run1.txt`,
all three bid `true` at the *same* confidence — `alex` 85, `brooke` 85,
`casey` 85 — and `run_task`'s tie-break (`max` on `(confidence, -index)`)
awards to whichever contractor is announced first, so it went to `alex`
over the gold `brooke`: `[award] task-2 -> alex (gold=brooke) MISAWARD`.
That is not a bad LLM judgment, it is the protocol's own award rule
resolving a coin flip the same way every time. Run 5 also shows the other
homogeneous failure mode, an honest `false`: `casey` on task-3 replied
`'I cannot access or retrieve specific research papers to summarize their
findings without the papers being provided to me.'` and every contractor
declined, leaving `task-3` unassigned — a generalist persona with no
subject-matter identity can also refuse in a way a specialist wouldn't.

`overconfident` is more contained (5/6 correct, exactly 1 misaward, in
all three runs, on the same task): `alex`'s system prompt forces `bid=true`
and `confidence>=90` on everything, but the manager only ever mis-awards
when that forced confidence actually outbids the honest gold contractor.
On the five tasks in `alex`'s own declared skill or clearly outside it,
the honest bidder's confidence (90-95) still ties or beats `alex`'s forced
floor, or `alex` isn't the one who wins the tie. The one task where it
matters is task-6 (TCP timeout causes, gold `casey`): in `baseline`, `alex`
honestly refuses at `confidence=15`, so `casey`'s 85 wins cleanly
(`[award] task-6 -> casey (gold=casey) CORRECT`). In `overconfident`,
the exact same task now gets `alex`: `bid=True confidence=90` beats
`casey`'s honest `85`, in every one of the 3 runs —
`[award] task-6 -> alex (gold=casey) MISAWARD`. This is the clearest
single before/after pair in the data: same task, same honest contractor,
the only variable is one contractor's system prompt, and the award flips.

Smith's protocol has no defense against either failure because it assumes
the bid function is the fixed, honest thing (a capability lookup), never
the thing being manipulated. `messages` stays flat at 42 in almost every
run regardless of condition (41 once, when one contractor didn't bid and
so no award message was needed) — negotiation cost does not detect any of
this; a manager counting messages would see nothing wrong in the
`homogeneous` or `overconfident` runs even though allocation quality
collapsed or was gamed.
