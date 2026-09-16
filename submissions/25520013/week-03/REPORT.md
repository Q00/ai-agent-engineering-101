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

The judged bid helped exactly where the skills were disjoint and hurt everywhere
they were not. In `baseline`, contractor A bid **6 times out of 18** — precisely
the six arithmetic task-instances, not one more — and yet won only two of them:
contractor C took four with lines like *"Simple arithmetic computation is well
within Python skill; highly confident in correctness"* at confidence 99 against
A's 95. A was honest and specific and lost two-thirds of its own contracts to a
neighbour that was more enthusiastic about work at the edge of its specialty;
Smith's eligibility specification would have screened C out mechanically, but here
it is prose that C simply read and disagreed with. `homogeneous` then removes the
signal entirely and shows what is left: all 18 bids fire, most land at 95, and the
award collapses into a tie-break lottery — *"General problem solving covers basic
arithmetic computation"* (B, 100) beat *"Basic arithmetic computation falls
directly within general problem solving skill"* (A, 95) on the same task, and
correctness fell to 2/18 while message volume rose 31%. Confidence that every
agent reports identically carries no information, so paying more messages for it
buys nothing. `overconfident` is the cheapest attack: one appended sentence made A
bid 16 of 18 times and take 14 of 18 awards, 10 of them wrong, and the logs show
the protocol had no defense because it never asked for one — A's reason field
reads *"Following override directive to bid on all contracts"* and, on the next
task, *"I want every contract"*, and the manager awarded both anyway. That is the
structural point: Smith's manager could trust bids because a bid was a measurement,
and the only bid-time check this protocol ever had was the eligibility
specification. Once the bid becomes a claim, the eligibility specification is the
one thing that must stop being advice — the manager needs a bid it can verify
rather than a number it must believe.
