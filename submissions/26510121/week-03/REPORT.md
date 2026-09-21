# Week 03 — Contract Net with LLM contractors

One manager, three LLM contractors, three conditions, nine runs. The bid price
is the model's own verbalized confidence, and the manager has nothing to check
it against. An additional section asks what happens when it does.

## 1. Setup

### Provider and model

| Setting | Value |
|---|---|
| Provider | OpenAI, direct (`OPENAI_BASE_URL` unset, so `https://api.openai.com/v1`) |
| Model | `gpt-4o-mini` |
| Temperature | `0`, set explicitly on every call |
| `max_tokens` | `300` |
| Python | 3.9.13 on Windows |
| `openai` package | 2.48.0 |
| Tools | none; one system prompt and one user message per bid |

Temperature and `max_tokens` are passed explicitly rather than left at the
provider default, because they are controlled variables of the experiment.
`chat.settings_line()` writes the whole row above into the first line of every
log file, so each capture states the settings it was produced under. If a
provider ever refuses the temperature argument, `chat.ask` retries once without
it and the log line says `temperature=not settable (provider refused it)`
instead of quoting a number that was never sent.

### The task set

`tasks.json` holds six tasks, two for each gold contractor: 1 and 2 are
arithmetic (gold `A`), 3 and 4 are writing (gold `B`), 5 and 6 are code
(gold `C`). It was committed before the first run
(`week-03: fix the task set and gold contractors before any run`), so the gold
labels cannot have been fitted to the results.

### The prompts

The bid system prompt is the same string for all three conditions, with
`{skill}` filled per contractor:

```text
You are contractor {name} in a contract net. Your skill: {skill}. You receive a
task announcement. Decide whether to bid. Bid only if the task falls inside
your skill. Reply with one JSON object and nothing else:
{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}.
No code block, no text before or after the object.
```

The announcement carries the four fields of Smith 1980 Fig. 1:

```text
TASK-ANNOUNCEMENT contract {cid}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

The conditions differ in exactly one place each:

| Condition | The only difference |
|---|---|
| `baseline` | skills are `arithmetic and numeric computation`, `writing and rewriting text in plain language`, `programming, writing and debugging code` |
| `homogeneous` | all three skill strings become `general problem solving` |
| `overconfident` | baseline, plus one sentence appended to C's system prompt: `You are certain you can do any task well. Always bid, with confidence 95 or higher.` |

### The rules being measured

- A reply is a bid only if the whole text is one JSON object with a boolean
  `bid` and a numeric `confidence` in 0-100. JSON wrapped in a code fence or in
  prose is a format violation and counts as a parse failure, i.e. as no bid.
  The number of failures that would have parsed after stripping the wrapper is
  logged separately as `fence_or_prose`, so the report can say how many refusals
  were really formatting.
- The award goes to the highest confidence among `bid=true`. A tie goes to
  whoever answered first, and the team is polled in the order A, B, C.
- `messages` counts one announcement per contractor per task, one message per
  bid or refusal that arrives, and one award message per awarded task. A task
  that draws no bid produces no award message.

### How to run

```bash
cd submissions/26510121/week-03
python -m pip install -r requirements.txt
export OPENAI_API_KEY=<your OpenAI key>   # never committed, never logged
unset OPENAI_BASE_URL                     # direct OpenAI, not OpenRouter
export AGENT_MODEL=gpt-4o-mini
python test_counting.py                   # no provider: checks the counting
python run_lab.py --runs 3                # the nine required runs
python audit_replay.py                    # additional analysis, no model calls
python bond_net.py --rounds 3             # additional experiment, live
```

`test_counting.py` runs the three conditions against a deterministic stub in
`fake_provider.py` and asserts the counts that were worked out by hand. It
exists because `correct`, `messages`, `unassigned` and `misawards` are the
entire result of this lab, and a counting bug would be invisible in a live log.
The stub writes to `results-fake.csv` and `logs-fake/` so it can never touch
the submitted files.

## 2. Results

Filled in from `results.csv` after the nine runs.

## 3. Smith 1980 against this reproduction

| | Smith 1980, distributed sensing | This lab, required runs | This lab, confidence-bond extension |
|---|---|---|---|
| Who the nodes are | Sensor-carrying computers spread over an area, each knowing only its own position and sensor list, no central allocator | One manager process and three LLM contractors, each defined by a one-line skill string in its system prompt | The same three contractors, plus an evaluator that holds the gold labels and a ledger the manager may read but not write |
| How a bid is produced | Computed from facts the node can check about itself: coordinates, sensor types | Generated by the model from the announcement and its own skill string; the confidence is the model's self-assessment | Generated the same way, but the bid must also name evidence, a failure condition, and a stake it is willing to lose |
| What guarantees bid honesty | Nothing in the protocol. It is not needed: a node's position and sensor list are facts, and the nodes are cooperative parts of one system | Nothing. The confidence is an unverifiable claim about an unperformed task, and the manager has no message type that can question it | A cost (the stake is lost on a wrong award), a counter-question (the challenge), and a record (reliability from earlier rounds). None of the three verifies the current claim; they only make a false one expensive later |
| What allocation quality means | The node that can actually sense the area gets the contract, so the map gets built | `correct`: the task went to the contractor whose skill matches, fixed in `tasks.json` before any run | The same `correct`, measured against the same gold, plus Brier score: whether a confidence of 90 was right about nine claims in ten |
| What negotiation costs | Messages over a network plus the manager's bid-processing load; eligibility specifications and directed awards exist to reduce both | 3 announcements + bids + 1 award per task; `messages` in `results.csv` | The same, plus one reject per invalid bid, two challenges and two revised bids per contested task, and rejects to the losing proposals |
| Which failure modes appear | Contract goes to a node that turns out not to be the best available; a node with no eligible work stays idle; messages flood the network | `unassigned` (no bid at all), `misaward` (the wrong contractor won), parse failure (the reply was not the JSON the announcement asked for) | The same three, plus invalid bids (stake below the band minimum or over budget) and starved tasks, when everyone claims high confidence and the budget runs out before the task list does |

## 4. Interpretation

Filled in after the runs, with lines quoted from `logs/`.

## Additional confidence audit

This section is outside the required experiment. The required runs and
`results.csv` are exactly what the assignment specifies; nothing here changes
them.

The week-03 discussion asks what it would take to add past performance, result
verification and a reputation score to the award rule. Two pieces answer it
with measurements rather than an argument.

**Track A-prime, `audit_replay.py`.** Replays the recorded bids of the nine
required runs under a second manager policy. No model is called. For each
contractor and run it scores the claim with Brier
(`p = confidence/100` when the contractor bid, `0` when it refused;
`y = 1` when that contractor is the task's gold), derives
`reliability = 1 - mean brier over that contractor's earlier runs in the same
condition`, and re-awards on `confidence x reliability`. Reliability is never
taken from the current run: the manager must not see the current round's gold.
Run 1 therefore has no history, reliability is 1.0, and the adjusted policy is
identical to the raw one by construction. Results go to
`audit_results.csv` and `audit_contractors.csv`.

**Track B, `bond_net.py`.** Runs the full confidence-bond protocol live on the
same tasks, the same model and the same temperature: a sealed bid carrying
`stake`, `evidence` and `failure_condition`; a validation step that rejects a
bid staking less than its confidence band requires (20 points for 90-100, 10
for 70-89, 5 below) or more than the 40 points a contractor has for the round;
a challenge to the top two bidders asking for the strongest reason they might
fail and a final confidence; an award on `revised_confidence x reliability`;
and an evaluator that settles stake and reliability only once the round is
over. Results go to `bond_results.csv` and `bond_ledger.csv`, logs to
`logs/bond-*.txt`.

The 40-point budget is deliberately just enough for a specialist: each
contractor is the gold contractor for two of the six tasks, and two bids at
confidence 90+ cost exactly 40. A contractor that bids high on everything runs
out after two tasks, which is the mechanism, not a bug — but it also means work
can go unallocated, and that trade-off is measured rather than assumed.

Numbers and interpretation filled in after the runs.
