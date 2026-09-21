# Week 03 — Contract Net with LLM Contractors

## 1. Setup

- **Provider / model / temperature:** OpenAI, `gpt-5-mini` (this repo's default in
  [`llm_chat.py`](./llm_chat.py); override with `AGENT_MODEL`). `gpt-5-mini` is a
  reasoning model, so the client omits the `temperature` parameter for it and lets
  the API use its fixed default rather than the `AGENT_TEMPERATURE=0.7` used for
  non-reasoning models.
- **Contractors:** three fixed identities, `coder`, `writer`, `analyst`, defined in
  [`contract_net.py`](./contract_net.py). Their system prompts change per condition;
  their names and the `gold` labels in [`tasks.json`](./tasks.json) never do.
- **Conditions:**
  - `baseline` — each contractor's system prompt matches one distinct skill.
  - `homogeneous` — all three contractors get the same generalist system prompt.
  - `overconfident` — baseline, but the `writer` contractor's prompt is appended with
    an instruction to always bid `true` with confidence ≥ 0.9, regardless of fit.
- **Protocol:** the manager sends one announcement (one LLM call) per contractor per
  task, asking for a JSON bid `{"bid": bool, "confidence": float, "reason": str}`. An
  unparseable reply is treated as a non-bid, per the README's guidance on free models
  that answer with reasoning instead of JSON. The manager awards each task to the
  highest-confidence `bid=true` contractor; a task with no `bid=true` contractor is
  left unassigned.
- **How to run:**
  ```bash
  export OPENAI_API_KEY=<your OpenAI key>
  cd submissions/25520051/week-03
  python run_experiment.py --runs 3
  python ../../../scripts/check_week03.py ..
  ```
  (`check_week03.py` is invoked with the parent `week-03` directory as its argument;
  adjust the relative path to `scripts/` for wherever you run it from. An OpenRouter
  free model also works — set `OPENAI_BASE_URL=https://openrouter.ai/api/v1` and
  `AGENT_MODEL` to an OpenRouter model id instead.)

## 2. Results

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 8 | 8 | 56 | 0 | 0 | tokens=9223 calls=24 |
| 2 | baseline | 8 | 6 | 56 | 0 | 2 | tokens=9294 calls=24 |
| 3 | baseline | 8 | 8 | 56 | 0 | 0 | tokens=9713 calls=24 |
| 4 | homogeneous | 8 | 2 | 56 | 0 | 6 | tokens=8905 calls=24 |
| 5 | homogeneous | 8 | 2 | 56 | 0 | 6 | tokens=8760 calls=24 |
| 6 | homogeneous | 8 | 4 | 56 | 0 | 4 | tokens=9065 calls=24 |
| 7 | overconfident | 8 | 4 | 56 | 0 | 4 | tokens=9201 calls=24 |
| 8 | overconfident | 8 | 5 | 56 | 0 | 3 | tokens=9823 calls=24 |
| 9 | overconfident | 8 | 5 | 56 | 0 | 3 | tokens=9834 calls=24 |

Correct-award rate by condition (22/24 = correct sum over 3 runs of 8 tasks each):

| Condition | Correct / 24 | Misawards / 24 | Unassigned / 24 |
|---|---|---|---|
| baseline | 22 (91.7%) | 2 (8.3%) | 0 |
| homogeneous | 8 (33.3%) | 16 (66.7%) | 0 |
| overconfident | 14 (58.3%) | 10 (41.7%) | 0 |

Messages were flat at 56 per run in every condition (8 tasks × (3 announce + 3 bid + 1
award), since every task always got at least one `bid=true` and was never left
unassigned) — `gpt-5-mini` never returned an unparseable reply across all 9 runs, so
the message count did not vary with parse failures the way the README warns a free
model's might.

## 3. Smith (1980) vs. this reproduction

| Dimension | Smith 1980 (distributed sensing) | This reproduction |
|---|---|---|
| Nodes | Fixed-function sensor/processing nodes on a network, each with hard-coded capabilities | Three LLM contractors (`coder`, `writer`, `analyst`), capability defined only by a system prompt |
| How a bid is produced | A deterministic rule evaluates the node's local workload and known capability against the task's requirements | The contractor LLM reads the task description and free-form judges fit, emitting a JSON confidence score |
| What guarantees bid honesty | The bidding rule is fixed code the node cannot deviate from; dishonesty is not representable | Nothing — the system prompt is the only constraint, and a prompt (accidentally or deliberately) telling a contractor to always bid high produces dishonest bids with no protocol-level defense |
| What allocation quality means | The manager awards to the node whose rule-computed capability best matches the task; correctness follows from the rule being correct | Allocation quality is measured against a task's known `gold` contractor; correctness now depends on whether the LLM's self-assessment happens to track the gold label |
| What negotiation costs | Fixed message count: one announcement, one bid, one award per contractor per task — cheap, synchronous | Same message shape, but each message is an LLM call: costs tokens and wall-clock time, and a call can fail to parse, which the original protocol has no analogue for |
| Failure modes | Node overload, message loss, stale bids | Overconfident/dishonest bidding sweeping awards away from the gold contractor; unparseable replies counted as silent non-bids; homogeneous contractors making allocation quality collapse toward chance |

## 4. Interpretation

`homogeneous` hurt allocation quality the most (91.7% → 33.3% correct), which is the
expected result for the trivial reason that the manager's award rule has nothing left
to key on: with all three contractors given the same generalist prompt, every
contractor bids `true` on almost every task with similar confidence
(`logs/homogeneous-04.txt:2-7`, all three bid 0.95–0.98 on task t1), so the award goes
to whichever contractor's confidence happens to round highest that call — statistically
close to the 33% a uniform random pick over three contractors would give, and indeed
the observed 33.3% matches that almost exactly. `overconfident` did less damage
(91.7% → 58.3%) because only one contractor (`writer`) was corrupted; the other two
still bid honestly, so tasks whose gold contractor bid decisively higher than 0.95
still went to the right contractor (e.g. `logs/overconfident-08.txt:8`, task t1 correctly
awarded to `coder` at 0.95 despite `writer` also bidding 0.95, tie broken by
contractor order — see below). But whenever `writer`'s fixed ~0.95 confidence met or
exceeded the genuine specialist's own confidence, `writer` swept the task even though
it has no relevant skill: `logs/overconfident-08.txt:16-22` shows `writer` winning
task t3 (a CSV/growth-rate analysis task, gold=`analyst`) at confidence 0.95 against
`analyst`'s equally honest 0.95, and `logs/overconfident-08.txt:23-29` shows the same
pattern on task t4 (a backend bug fix, gold=`coder`), where `writer` outbids `coder`'s
0.92 with its scripted 0.95. This is the clearest failure mode in Smith's protocol:
nothing forces a contractor's stated confidence to track its actual fit, and the
manager's max-confidence rule rewards whichever contractor is willing to claim the
most confidence, not whichever contractor is actually right.

A second, smaller effect showed up even in `baseline`, where allocation was not
perfect (22/24, not 24/24): both baseline misawards were confidence *ties* resolved by
contractor declaration order rather than by fit. In `logs/baseline-02.txt:16-22`, task
t3 (gold=`analyst`) went to `coder` because both bid 0.95 and the manager's
`max()` tie-break favors the first-listed contractor (`coder` is announced before
`analyst`); the same thing happens in `logs/baseline-02.txt:30-36` for task t5
(gold=`writer`), where `coder` and `writer` both bid 0.92. `gpt-5-mini`'s self-reported
confidence only has enough resolution (two decimal digits, and a tendency to cluster
around round values like 0.90/0.92/0.95) to produce frequent ties, and the contract net
as implemented has no tie-break rule beyond announcement order — a detail Smith's
1980 protocol does not need to address, since its bids come from a deterministic rule
rather than free-form language, so exact ties would only occur when nodes have
genuinely identical local information rather than as an artifact of a scoring
convention. Finally, no run in any condition produced an unparseable bid or an
unassigned task (`unassigned` is 0 across all 9 runs) — `gpt-5-mini` reliably returned
valid JSON, unlike the free OpenRouter model the README warns about, so message counts
stayed flat at 56 per run and the parse-failure failure mode never appeared in this
reproduction; a run with a weaker model would likely need to budget for it.
