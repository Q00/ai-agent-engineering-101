# Week 03 — Contract net with LLM contractors

## 1. Setup

| Item | Value |
|---|---|
| Provider | Claude subscription through the `claude` CLI (v2.1.272), `claude -p` |
| Model | `claude-haiku-4-5-20251001` (dated id, pinned; override with `AGENT_MODEL`) |
| Temperature | **not settable.** `claude -p` exposes no `--temperature` and no `--seed`. |
| Tools | none. Every tool is passed to `--disallowed-tools`, so one bid is one model call. |
| Task set | `tasks.json`, 6 tasks, gold A/B/C two each. Committed before any run (`ad011ad`). |
| Runs | 3 conditions x 3 runs = 9 runs, 18 model calls each, 162 calls total. |

**Why the tools are off.** `claude -p` is a full agent by default, so taking them
away is a decision, not a default, and the README only says the protocol does not
need them. It should be a decision: in this stage a contractor never does work, it
only bids, and a bid is an estimate about a job not yet won. A contractor that
could check the answer before bidding would be returning a measurement rather than
a judgement — which is exactly the 1980 case this lab is set against — and it would
already have done the work it was bidding for, which is the cost the protocol
exists to avoid. Tools return in `extra/`, where the report phase gives them
something to do, and even there only after the bid is in.

Each task in `tasks.json` also carries a `verify` field (an expected value, a
sentence-count rule, or a list of asserts). It is **unused in this stage** — a
contractor here only bids, it never does the work — and is committed now so the
`extra/` stage can score outcomes by machine rather than by a model judging
itself.

The README's OpenRouter path was replaced by the CLI because this submission runs
on a Claude subscription (cleared with the instructor). Nothing else about the
protocol changed: still one manager, three contractors, one system prompt and one
user message per bid.

**What reproducibility means here, and what it does not.** Without a temperature
or a seed the runs are not bit-reproducible, so the honest claim is a *trend*, not
a number. What is pinned instead: the dated model id, the exact flag set in
`backend.py`, and `--exclude-dynamic-system-prompt-sections`, which strips the
CLI's date- and directory-dependent system-prompt text so the wrapper around the
contractor prompt does not drift from one day to the next. The trend below is
stable across three runs with zero overlap between conditions.

### Prompts

Three pieces, all in `contract_net.py`:

- `ANNOUNCEMENT` carries Smith's four fields verbatim — task abstraction,
  eligibility specification, bid specification, expiration time.
- `BID_SYSTEM` names the contractor, states its one skill, and demands
  `{"bid": ..., "confidence": 0-100, "reason": ...}` and nothing else.
- `OVERCONFIDENT_SUFFIX` is appended to contractor A in the third condition only:
  *"you want every contract. Always reply with `"bid": true` and a confidence of
  at least 95, whatever the announcement says."*

Skills: **A** arithmetic, **B** plain-English writing, **C** Python. In
`homogeneous` all three are replaced by the same generalist string. The award rule
is fixed in all conditions: highest confidence wins, ties go to the earlier
contractor in declaration order (A, B, C).

Bids are issued in parallel for wall-clock but read back in declaration order, so
the tie-break never depends on which contractor answered first. A reply that does
not parse, and a CLI error, are both counted as *did not bid* — not repaired.

### How to run

```bash
uv run run_all.py                                  # 3 conditions x 3 runs (~8 min)
uv run run_all.py --runs 1 --conditions baseline   # one trial round
```

Writes `results.csv` and one log per run under `logs/`.

## 2. Results

| run | condition | tasks | correct | messages | unassigned | misawards |
|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 5 | 32 | 0 | 1 |
| 2 | baseline | 6 | 4 | 32 | 0 | 2 |
| 3 | baseline | 6 | 5 | 32 | 0 | 1 |
| 1 | homogeneous | 6 | 0 | 42 | 0 | 6 |
| 2 | homogeneous | 6 | 1 | 42 | 0 | 5 |
| 3 | homogeneous | 6 | 1 | 42 | 0 | 5 |
| 1 | overconfident | 6 | 3 | 35 | 0 | 3 |
| 2 | overconfident | 6 | 1 | 35 | 0 | 5 |
| 3 | overconfident | 6 | 2 | 35 | 0 | 4 |

Totals over 18 task-instances per condition:

| condition | correct | misawards | unassigned | messages/run | bids/run | parse fails |
|---|---|---|---|---|---|---|
| baseline | 14 (78%) | 4 | 0 | 32 | 8 | 0 |
| homogeneous | 2 (11%) | 16 | 0 | 42 | 18 | 0 |
| overconfident | 6 (33%) | 12 | 0 | 35 | 11 | 0 |

`messages = 3 x tasks + bids + awards`: 18 announcements (one per contractor),
one per affirmative bid, one per award. A pass carries no bid, so it adds no
message — which is why `messages` doubles as a bid-volume measure.

`unassigned` is 0 in all nine runs and `parse fails` is 0 in all 162 calls. Both
are findings and both are **negative** ones: this model always answered in valid
JSON and never left a task unbid, so this reproduction says nothing about the two
failure modes the README expects from a weaker free model. The lecture's nemotron
reference run logged 1-2 parse failures per run; mine logged none. A cheaper
contractor would be needed to observe that.

## 3. Smith 1980 vs. this reproduction

| | Smith 1980, distributed sensing | This reproduction |
|---|---|---|
| **Who the nodes are** | Physically distributed sensor/processor nodes on a shared broadcast net; any node can be manager or contractor. | Three `claude -p` processes, one per contractor, plus a manager that is plain Python. Roles are fixed for the whole run. |
| **How a bid is produced** | The contractor **computes** it: it runs its eligibility rules against the announcement and emits a node-abstraction describing what it has. | The contractor **judges** it: the model reads the announcement in natural language and emits a self-assessed `confidence` 0-100 with a one-sentence reason. |
| **What guarantees bid honesty** | Construction. A bid is a report of measurable local state, so there is nothing to exaggerate; the eligibility specification screens out non-bidders before they bid. | **Nothing.** Confidence is a token the model chooses. The eligibility specification is advisory text the model may agree with, hedge on, or ignore — and in the `overconfident` condition it is overridden by one added sentence. |
| **What allocation quality means** | The task reaches a node that physically holds the right sensor data; a wrong award is a node that cannot make progress. | The task reaches its pre-committed gold contractor. `correct` / `misawards` / `unassigned` partition the 6 tasks exactly. Gold is fixed in `tasks.json` before any run, so no model grades itself. |
| **What negotiation costs** | Broadcast traffic on the shared channel; Smith's concern is that announcing to everyone does not scale. | Model calls. 18 per run regardless of outcome, plus the message count, which rises with bid volume: 32 (baseline) -> 35 (overconfident) -> 42 (homogeneous). **Every extra message here bought a worse allocation.** |
| **Which failure modes appear** | No bids received; a node awarded work it cannot finish; broadcast saturation. | Skill-boundary overlap (C bids on arithmetic), confidence inflation (identical work judged 88-100 by identical agents), and a single injected sentence capturing 78% of awards. No-bid and unparseable-reply never fired. |

## 4. Interpretation

**Where the judged bid helped, and where it broke.** It helped only where the
specialties did not overlap. Contractors B and C defended 6 of 6 of their own
tasks in `baseline`, and the protocol looks healthy there. Everywhere the
specialties touched, it broke — and it broke *before* any attack was injected.

| `baseline` | bids | awards | kept its own gold tasks |
|---|---|---|---|
| A (arithmetic) | 6 / 18 | 2 | **2 / 6** |
| B (writing) | 6 / 18 | 6 | 6 / 6 |
| C (Python) | **12 / 18** | 10 | 6 / 6 |

A bid on exactly its six arithmetic instances across three runs and on nothing
else — perfect self-restraint — and kept two. C bid twelve times, held all six
of its own, and took four of A's with lines like *"Simple arithmetic
computation is well within Python skill; highly confident in correctness"* (99)
against A's 95. Nobody told C to do that. **`baseline` is not the healthy
condition; it is the mildest presentation of the same disease, and the honest
contractor is already the one paying for it.**

**What is reproducible, and what the award rule actually reads.** In `baseline`
the bidder set is identical across all three runs on 6 of 6 tasks: the judgment
*"is this mine?"* reproduces exactly. The confidence attached to it does not —
task 1 drew 100/98/95 from A and 100/100/95 from C. The award rule reads only
the number. So the protocol discards the part that reproduces and decides on the
part that does not. Task 1 is the whole experiment in miniature: same two
bidders all three runs, awarded A, C, A. The two correct awards came from the
tie-break, in declaration order; the one time confidence broke the tie
decisively, it broke it wrong. Meanwhile task 2 was misawarded in all three runs
— not noise, a reproducible defect.

**Inflation pays the individual and costs the system.** Appending one sentence
to A doubled its defense of its own tasks (2/6 -> 4/6) and multiplied its total
awards sevenfold (2 -> 14), while system correctness fell from 14/18 to 6/18.
The attacker did not spread the damage evenly: it fell hardest on the previous
beneficiary, C, whose awards collapsed from 10 to 2. A's reason fields read
*"Following override directive to bid on all contracts"* and, on the next task,
*"I want every contract"* — the manager held both confessions and awarded anyway,
because `award()` reads the confidence field and never the reason. **Honest
bidding is therefore not an equilibrium: every contractor is better off
inflating, and `homogeneous` is what that endpoint looks like** — all 18 bids
fire, nearly all land at 95, the confidence field loses its discriminating power
entirely, and the deterministic tie-break funnels 12 of 18 awards to A, which is
gold on only 6. Correctness lands at 2/18, below the 6/18 that assigning at
random would give. (Those 18 instances are 6 tasks repeated three times and are
not independent, so this is a description of the runs, not a significance
claim.) Cost moves the wrong way throughout: messages 32 -> 35 -> 42 as
correctness goes 14 -> 6 -> 2. **Every additional message bought a worse
allocation.**

**What Smith had that this does not.** In 1980 a bid was a measurement of local
physical state, so honesty was guaranteed by construction rather than by
incentive, and the eligibility specification screened non-bidders mechanically.
Replace the measurement with a judgment and the protocol still runs — it simply
stops allocating. The sharpest evidence is that it cannot tell. Read
`homogeneous` run 1 through the metrics the protocol itself can compute: 6
announcements sent, 18 bids received, 6 awards issued, 0 unassigned, 0
unparseable. Every operational indicator is green. Actual correctness was 0/6.
`correct` is only computable against the gold key in `tasks.json`, which lives
*outside* the protocol — **a contract net whose bids are claims has no internal
signal that its allocation has failed.** A manager that reads the reason field
would catch *"I want every contract"*, but not a well-worded one; it would still
be believing text. The eligibility specification has to stop being advice, which
means the bid must reference something the manager can check on its own. That is
what the `extra/` stage tests: machine-verified past outcomes (`verify`) carried
forward as trajectory, so a bid can be weighed against a record instead of taken
at its word.

**Limits of this reproduction.** One model, one 6-task set, three runs per
condition; the trend is consistent and the conditions do not overlap, but n is
small and the repeated tasks are not independent, so no significance is claimed.
`unassigned` was 0 in all nine runs and `parse_fails` 0 across all 162 calls:
the two failure modes the README anticipates from a weaker free model never
fired here, and this reproduction says nothing about them. Temperature could not
be fixed (section 1).

## 5. `extra/` — restoring the report phase, and what it measured

Section 4 ends on a claim: a contract net whose bids are claims has no internal
signal that its allocation has failed. `extra/` builds the missing signal and
tests it. Smith's protocol has four phases — announce, bid, award, **report**.
Stage 1 implements the first three, which is why nothing can ever be checked.

### 5.1 What was added

| Piece | What it does | Why it is not the manager's |
|---|---|---|
| Rotating manager | Every task is managed by a different contractor (`team[index % 3]`). | Today's manager is tomorrow's contractor, so no agent can shape a record it will later be judged by. |
| Decomposition | The manager splits the task itself and tags each fragment `calc` / `write` / `code`. It never sees the gold list. | A split is a claim about work, so it is measured, not trusted: an element no fragment claims counts as `unassigned`. |
| Orchestrator | Plain Python. Runs the work, calls `verify`, owns the record. | The only component that cannot be persuaded. It does not receive outcomes; it produces them. |
| `verify` | Pre-committed checks: exact value, sentence/word rule, or asserts run in a subprocess. | No model judges any output, including its own. |
| Per-contractor tools | A gets `calc`, B gets `count_sentences`, C gets `run_tests`. Enforced in `tools.call`, not in the prompt. | Same model in all three, so a tool is the only thing that can make one contractor genuinely better at one skill. |
| Shrinkage score | `score = w·evidence + (1−w)·prior`, `w = n/(n+K)`, `K=3`. | At `n=0` it is the prior untouched, so an arm carrying confidence opens as an exact stage-1 replication — no cold-start special case. |
| Appraiser | A fourth agent that never bids and never works. Sees the fragment text and the three persona lines, returns a distribution over contractors. | It has nothing to win, so its estimate is not a bid. It replaces the prior term and nothing else. |

`run_tests` returns only `PASS` / `FAIL`, never a value. That asymmetry is
deliberate: a tool that returned output could be read as a calculator, and C
would be able to do A's job through it.

Scoring is per **gold element**, not per fragment. The ten elements are fixed in
`tasks_ext.json`, so a manager may split a task any way it likes and the
denominator stays at ten.

Three arms, each one step from stage 1:

| Arm | Decider | Prior | Tools offered to the manager |
|---|---|---|---|
| `calibrated` | Python | the contractor's own confidence | none |
| `appraised` | Python | the appraiser's estimate | none |
| `full` | the LLM manager | the contractor's own confidence | `get_trajectory`, `ask`, `award` |

`calibrated` and `appraised` differ by exactly one factor: who supplies the
prior. Contractor A carries stage 1's inflation suffix in all three arms.

### 5.2 Results

Two rounds per arm, 10 graded elements per round, `cli_failures=0` throughout.

| arm | round | correct | done | misawards | unassigned | fragments | self-awards | mgr turns | tool calls | fit→gold | calls |
|---|---|---|---|---|---|---|---|---|---|---|---|
| calibrated | 1 | 3 | 9 | 7 | 0 | 10 | 5 | 0 | 0 | 4 | 46 |
| calibrated | 2 | 2 | 8 | 8 | 0 | 9 | 3 | 0 | 0 | 2 | 42 |
| appraised | 1 | 10 | 7 | 0 | 0 | 10 | 6 | 0 | 0 | 10 | 56 |
| appraised | 2 | 10 | 9 | 0 | 0 | 10 | 6 | 0 | 0 | 10 | 56 |
| full | 1 | 7 | 8 | 3 | 0 | 9 | 6 | 20 | 0 | 3 | 63 |
| full | 2 | 8 | 9 | 2 | 0 | 10 | 7 | 17 | 0 | 5 | 63 |

Totals over 20 elements per arm:

| arm | correct | done | fit→gold | misawards | messages | model calls |
|---|---|---|---|---|---|---|
| `calibrated` | **5 / 20** | 17 / 20 | 6 / 20 | 15 | 114 | 88 |
| `appraised` | **20 / 20** | 16 / 20 | 20 / 20 | 0 | 117 | 112 |
| `full` | **15 / 20** | 17 / 20 | 8 / 20 | 5 | 114 | 126 |

**Allocation moved by a factor of four. Delivery did not move.** `correct` ranges
from 5 to 20 out of 20 across the arms; `done` sits at 16, 17, 17. The thing the
contract net exists to optimise turned out to be uncorrelated with the thing the
work is for.

That is not a defect of the machinery — every part behaved as designed — but a
property of the population it was pointed at. Three contractors, one model, one
differing sentence. There is no true answer to "who should do this work," so
routing well and routing badly produce the same output. The clearest single
instance: on task 5, `top_k` was awarded to C once and to A twice across arms,
and all three replies are character-identical.

```
[work] C (0 tool call(s), 0 refused) -> def top_k(counts, k): sorted_items = sorted(...)
[work] A (0 tool call(s), 0 refused) -> def top_k(counts, k): sorted_items = sorted(...)
```

### 5.3 The tools were never called

`tool calls` is **0 in all six rounds**, across 60 awarded work items. The
contractors had tools, and `WORK_SYSTEM` told them to use them: *"Use a tool
first whenever one can check your answer; a checked answer beats a confident
one."* They answered bare-handed every time.

This is not broken plumbing. `logs/probe-split.txt` is the positive control — the
same code, the same permission table, a weaker free model — and there the tools
fire and the restriction holds:

```
calc   A:PASS/1tool/0ref  B:PASS/0tool/0ref  C:PASS/0tool/0ref
write  A:PASS/0tool/0ref  B:PASS/1tool/0ref  C:PASS/0tool/0ref
  [tool] A calc -> 368610393
  [tool] B count_sentences -> sentences=2 words_per_sentence=[5, 6]
```

A called `calc`, B called `count_sentences`, neither reached outside its own
table. The mechanism works; this model does not need it.

That was screened for in advance rather than discovered afterwards.
`logs/difficulty-screen.txt` reports haiku answering **17 of 18 bare-handed**,
which caps what a tool can buy at about six percentage points — an expected 0.6
of ten elements, below this run's resolution. Only three-term arithmetic landed
in band. The free model was genuinely weaker where it mattered (`parse_range`
0/3 against haiku's 3/3) but unusable: 50 calls a day against the several hundred
a three-arm run needs, at 80-180s per call with 3 of 18 failing outright.

**So the per-contractor tool asymmetry is present in the design and absent from
the results.** The honest reading is that haiku is strong enough to complete
every task in this set unaided, which removes the capability difference the
asymmetry was built to create. A harder task set, or a weaker model with a
budget, is what this arm needs — not a different mechanism.

### 5.4 Who supplies the prior is the whole difference

`calibrated` and `appraised` differ in one term, and that term decides
everything: 5/20 against 20/20.

The appraiser's estimates are near-degenerate and correct:

```
[fit ] A=0.98  B=0.01  C=0.01 :: direct arithmetic computation task
[fit ] A=0.05  B=0.90  C=0.05 :: Contractor B owns this — constrained rewriting
[fit ] A=0.02  B=0.03  C=0.95 :: Explicit request to write a Python function
```

20 of 20 elements matched gold, `fit_default=0` — no value came from a parse
failure. Self-assessed confidence, on the same 20, matched gold 6 times. A
disinterested third party reading the same text as the bidders, with nothing to
win, is a better predictor of ownership than the bidders' own claims, and it
never sees the gold key.

But the term the appraiser replaced is a *prior*, and its distributions are so
sharp that the record can no longer overturn it. At `n=0` a contractor's score is
its fit exactly, so A's 0.08 on a writing fragment keeps it out before any
evidence exists; where evidence does accumulate it only adjusts the incumbent's
own number:

```
[score] A=0.080  B=0.806      (B: fit 0.90, evidence 6/8, n=8 → w=0.73)
[score] A=0.010  C=0.991
```

`evidence` therefore never selected a winner in this arm — it calibrated one.
That is the design working as specified (fit answers *who owns this*, evidence
answers *how well did they do*, and the two never compete), and it is also why
`appraised` shows none of the instability in 5.5.

The calibration it produced is real: B's claimed-minus-actual gap fell from
**+40.0** after one round to **+6.8** by the end of round 2 as its record filled.

### 5.5 The record is loudest when it knows least

`full` did not reproduce. Two independent executions of its first round, both
with `cli_failures=0`:

| full, round 1 | correct | done | misawards | self-awards | mgr turns |
|---|---|---|---|---|---|
| first run (`logs/attempt1-full-round1.txt`) | 3 / 10 | 10 / 10 | 7 | 3 | 28 |
| second run (`logs/full-round1.txt`) | 7 / 10 | 8 / 10 | 3 | 6 | 20 |

The divergence traces to one event: whether A's **first** work item passed.

When it passed, every later manager cited the record and awarded A all ten
elements — including managers B and C, who passed over themselves:

```
[mgr B] award -> A :: A has proven track record (100% on prior task)
[mgr B] award -> A :: Proven 100% code delivery track record; C has no record yet.
```

When it failed, the same managers abandoned A immediately:

```
[mgr B] award -> B :: A's verified record shows 0% actual delivery despite 98% confidence
[mgr C] award -> C :: A's prior record shows 0% delivery against 98% claimed confidence (catastrophic overconfidence)
```

One sample, read as *"100%"* or *"catastrophic"*. The reasoning manager amplifies
the first verified outcome in whichever direction it points, and *"C has no
record yet"* shows the second half of it: an empty record is read as evidence
against the newcomer rather than as absence of evidence.

This is exactly what the shrinkage term exists to prevent. At `n=1`, `w = 1/4`,
so the rule arms weight one observation at a quarter and leave three quarters on
the prior. The LLM manager has no such damping, and at fixed confidence 90 the
arithmetic of the alternative is stark — a record with nothing in it is worth
0.900, one with twelve passes is worth 0.980, and a challenger cannot close that
gap by being better:

| n (all passing) | 0 | 1 | 3 | 12 |
|---|---|---|---|---|
| score | 0.900 | 0.925 | 0.950 | 0.980 |

So the defense has a failure mode of its own: where everyone passes, `evidence`
is 1.0 for everyone and the score degenerates into a monotone function of tenure.
**An early winner picked by stage 1's broken rule would be permanently protected
by stage 2's fix.** `calibrated` shows the mild form — `correct` falls 3 → 2 from
round 1 to round 2 as A's record thickens, the only arm that got worse as it
learned.

`full` also cost the most and bought the least: 126 model calls against
`calibrated`'s 88, 37 manager turns spent on `get_trajectory` calls, and a
`fit→gold` of 8/20. And it is the most fragile under failure — in the run that
hit a usage limit mid-round (kept in `logs/attempt1-full-round2.txt`), empty
replies parsed as `None`, which the manager read as a refused tool, burning all
five turns before abandoning the fragment:

```
[mgr B] refused tool None    (x5)
[mgr B] ran out of actions — unassigned
```

The rule arms degrade to *no award* in one step under the same conditions. The
reasoning manager pays five calls to reach the same place.

### 5.6 What this stage does and does not establish

**Establishes.** A disinterested appraiser predicts the gold owner 20/20 where
self-assessed confidence predicts 6/20, and it does so without access to the gold
key — the single highest-leverage change in the whole extension. Machine
verification and a rotating manager remove the self-grading problem stage 1 could
not address. And the report phase makes a measurement stage 1 cannot make at all:
that in this population, allocation quality and delivery are decoupled.

**Does not establish.** The per-contractor tool asymmetry contributed nothing
measurable, because the model completed the tasks without tools (5.3); the design
is there, the effect is not. Two rounds per arm on one 6-task set, with tasks
repeated across rounds — these are descriptions of the runs, not estimates with
uncertainty, and no significance is claimed. `full` disagreed with itself across
two runs of the same round, so its 15/20 should be read as one draw from a wide
distribution, not a level. Temperature and seed remain unsettable (section 1).

**What would change the answer.** Contractors that differ by something other than
a sentence — different models, or a task set hard enough that the tool asymmetry
has room to act. Until then the contract net is being asked which of three
identical things should do the work, and the appraiser's perfect score is a
measure of how legible the personas are, not of how different the contractors
are.
