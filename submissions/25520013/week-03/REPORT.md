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
