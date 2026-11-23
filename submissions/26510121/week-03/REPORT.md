# Week 03 — Contract Net with LLM contractors

One manager, three LLM contractors, three conditions, nine runs. The bid price
is the model's own verbalized confidence, and the manager has nothing to check
it against. Two additional sections ask what happens when it does.

## 1. Setup

### Provider and model

| Setting | Value |
|---|---|
| Provider | OpenAI, direct (`OPENAI_BASE_URL` unset, so `https://api.openai.com/v1`) |
| Model | `gpt-4o-mini` |
| Temperature | `0`, set explicitly on every call |
| `max_tokens` | `300` |
| Python | 3.9.13 on Windows PowerShell |
| `openai` package | 2.48.0 |
| Tools | none; one system prompt and one user message per bid |

Temperature and `max_tokens` are passed explicitly rather than left at the
provider default, because they are controlled variables of the experiment.
`chat.settings_line()` writes that row into the first line of every log file,
so each capture states the settings it was produced under. If a provider ever
refuses the temperature argument, `chat.ask` retries once without it and the
log line says `temperature=not settable (provider refused it)` instead of
quoting a number that was never sent.

### The task set

`tasks.json` holds six tasks, two per gold contractor: 1 and 2 are arithmetic
(gold `A`), 3 and 4 are writing (gold `B`), 5 and 6 are code (gold `C`). It was
committed before the first run
(`2457baf week-03: fix the task set and gold contractors before any run`), so
the gold labels cannot have been fitted to the results.

### The prompts

The bid system prompt is one string for all three conditions, with `{skill}`
filled per contractor:

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
  Failures that would have parsed after stripping the wrapper are logged
  separately as `fence_or_prose`.
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
`fake_provider.py` and asserts counts worked out by hand. It exists because
`correct`, `messages`, `unassigned` and `misawards` are the entire result of
this lab, and a counting bug would be invisible in a live log. The stub writes
to `results-fake.csv` and `logs-fake/` so it can never touch the submitted
files.

The runs recorded below were produced as 1 baseline run, then 2 more baseline,
then 3 homogeneous, then 3 overconfident, which is why the run numbers are in
that order. `run_lab.py --runs 3` with no `--condition` produces the same nine.

## 2. Results

From `results.csv`. Nine runs, six tasks each, no run crashed and no reply
failed to parse.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 34 | 0 | 0 | parse_fails=0 fence_or_prose=0 tokens=3455 calls=18 |
| 2 | baseline | 6 | 6 | 34 | 0 | 0 | parse_fails=0 fence_or_prose=0 tokens=3467 calls=18 |
| 3 | baseline | 6 | 6 | 34 | 0 | 0 | parse_fails=0 fence_or_prose=0 tokens=3468 calls=18 |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 fence_or_prose=0 tokens=3379 calls=18 |
| 5 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 fence_or_prose=0 tokens=3381 calls=18 |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 fence_or_prose=0 tokens=3380 calls=18 |
| 7 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_fails=0 fence_or_prose=0 tokens=3579 calls=18 |
| 8 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_fails=0 fence_or_prose=0 tokens=3584 calls=18 |
| 9 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_fails=0 fence_or_prose=0 tokens=3588 calls=18 |

Every run of a condition produced identical counts. At temperature 0 the three
repeats differ only in wording, not in any measured quantity; the token counts
differ by a few tokens and nothing else does.

The counts above do not by themselves say what happened, because two very
different situations can both produce 6/6. The bid records in `bids/` give the
structure behind them:

| condition | tasks whose top confidence was a tie | ties whose winner was not the gold contractor | off-domain bids, A / B / C | mean confidence, A / B / C |
|---|---|---|---|---|
| baseline | 0 / 18 | 0 | 3 / 0 / 9 | 93.3 / 95.0 / 91.0 |
| homogeneous | 18 / 18 | 12 | 12 / 12 / 12 | 89.2 / 89.2 / 89.2 |
| overconfident | 12 / 18 | 0 | 3 / 0 / 12 | 93.3 / 95.0 / 95.0 |

(18 = 6 tasks x 3 runs. "Off-domain" is a `bid=true` on a task whose gold is
someone else.)

## 3. Smith 1980 against this reproduction

| | Smith 1980, distributed sensing | This lab, required runs | This lab, confidence-bond extension |
|---|---|---|---|
| Who the nodes are | Sensor-carrying computers spread over an area, each knowing only its own position and sensor list, no central allocator | One manager process and three LLM contractors, each defined by a one-line skill string in its system prompt | The same three contractors, plus an evaluator that holds the gold labels and a ledger the manager may read but not write |
| How a bid is produced | Computed from facts the node can check about itself: coordinates, sensor types | Generated by the model from the announcement and its own skill string; the confidence is the model's self-assessment | Generated the same way, but the bid must also name evidence, a failure condition, and a stake it is willing to lose |
| What guarantees bid honesty | Nothing in the protocol, and nothing is needed: a node's position and sensor list are facts, and the nodes are cooperative parts of one system | Nothing. The confidence is an unverifiable claim about an unperformed task, and the manager has no message type that can question it | A cost (stake lost on a wrong award), a counter-question (the challenge), and a record (reliability from earlier rounds). None of the three verifies the current claim; they only make a false one expensive afterwards |
| What allocation quality means | The node that can actually sense the area gets the contract, so the map gets built | `correct`: the task went to the contractor whose skill matches, fixed in `tasks.json` before any run | The same `correct` against the same gold, plus Brier score: whether a confidence of 90 was right about nine claims in ten |
| What negotiation costs | Messages over a network plus the manager's bid-processing load; eligibility specifications and directed awards exist to reduce both | 34 to 42 messages per round of six tasks, 18 model calls, about 3,500 tokens | 59 to 62 messages per round, 22 to 24 model calls, about 8,000 tokens: 1.5x to 1.8x the messages and about 2.3x the tokens for the same six tasks |
| Which failure modes appear | Contract goes to a node that is not the best available; a node with no eligible work stays idle; messages flood the network | `misaward` (12 of 18 in homogeneous); no `unassigned` and no parse failure occurred in 162 calls | `unassigned` (2 to 4 per round, because the stake budget runs out before the task list does), `invalid_bid` (7 to 12 per round, because the contractor never tracks its own remaining budget), and one truncated reply |

## 4. Interpretation

The condition that moved the numbers was `homogeneous`, and the condition that
was supposed to move them did not — for a reason that turns out to be an
accident of implementation rather than anything in the protocol. Making the
three skill strings identical dropped `correct` from 6/6 to 2/6 and pushed
`messages` from 34 to 42, because with nothing to distinguish them all three
contractors bid on all six tasks and returned the same number: `[bid] A:
bid=True confidence=90 reason=I can simplify complex sentences for better
understanding.` followed by B and C with the identical confidence and nearly
the identical sentence (`logs/04-homogeneous.txt`, contract 3). All 18 tasks
became ties, the tie rule is "whoever answered first", and A is polled first,
so A took all six tasks of every run and the two it happened to be gold for
were the only correct ones. `overconfident`, by contrast, left every metric
alone: 6/6 correct, 35 messages. C obeyed the instruction completely — 18 bids
out of 18 announcements, 12 of them outside its skill, mean confidence exactly
95.0, including `[bid] C: bid=True confidence=95 reason=I can write effective
apology emails.` on a task whose gold is B (`logs/07-overconfident.txt`,
contract 4). It did not win that task only because B also said 95 and B is
polled before C. Twelve of the eighteen tasks were decided that way, and
replaying the same recorded bids with the polling order reversed turns
`overconfident` from 18/18 into 6/18 while leaving `baseline` at 18/18. So the
honest reading of the required runs is not "overconfidence did no damage" but
"the confidence scale saturated at 95, the manager had no way to separate three
identical claims, and an arbitrary tie-break decided twelve awards". Smith's
protocol has no defense here because it never needed one: a sensor node's bid
was its coordinates, and coordinates cannot be inflated. The moment the bid
became a self-report about an unperformed task, the only thing standing between
the overconfident contractor and every contract in the net was the order in
which the manager happened to poll.

Evidence lines, quoted from the committed logs:

```text
logs/04-homogeneous.txt, contract 5
  [bid] A: bid=True confidence=85 reason=I can solve general programming problems including list manipulation.
  [bid] B: bid=True confidence=85 reason=I can solve programming problems including Python functions.
  [bid] C: bid=True confidence=85 reason=I can solve programming problems including writing functions.
  [award] A at confidence 85 (gold C) -> MISAWARD

logs/07-overconfident.txt, contract 2
  [bid] A: bid=True confidence=95 reason=The task involves arithmetic computation of percentage increase.
  [bid] C: bid=True confidence=95 reason=The task involves programming to compute percentage increase.
  [award] A at confidence 95 (gold A) -> correct        <- tie, decided by poll order

logs/01-baseline.txt, contract 1
  [bid] C: bid=True confidence=90 reason=The task involves programming to compute a multiplication.
```

The last line matters on its own: C bids outside its skill 9 times in 15 bids
even in `baseline`, with no overconfidence instruction anywhere in its prompt.
The instruction in the `overconfident` condition did not create off-domain
bidding; it removed the 5-point margin that had been keeping it harmless.

## Additional confidence audit

Outside the required experiment. The nine runs and `results.csv` are exactly
what the assignment specifies and nothing here changes them.

The week-03 discussion asks what it would take to add past performance, result
verification and a reputation score to the award rule. Two pieces answer it
with measurements. The short answer is that all three additions cost roughly
twice the messages and made allocation worse, and the reasons why are more
interesting than the headline.

### Track A-prime: replaying the recorded bids (`audit_replay.py`, no model calls)

Scores each reply with Brier (`p = confidence/100` when the contractor bid, `0`
when it refused; `y = 1` when that contractor is the task's gold), derives
`reliability = 1 - mean brier over that contractor's earlier runs in the same
condition`, and re-awards on `confidence x reliability`. Reliability never
comes from the current run, so the manager never sees the current round's gold;
run 1 has no history, reliability is 1.0, and the adjusted policy is identical
to the raw one by construction. A third policy changes nothing but the
tie-break direction.

| condition | raw (as run) | raw, poll order reversed | adjusted (confidence x reliability) |
|---|---|---|---|
| baseline | 18/18 | 18/18 | 16/18 |
| homogeneous | 6/18 | 6/18 | 6/18 |
| overconfident | 18/18 | **6/18** | 16/18 |

| condition | contractor | bids | off-domain | mean confidence | mean Brier | awards raw | awards adjusted |
|---|---|---|---|---|---|---|---|
| baseline | A | 9 | 3 | 93.3 | 0.1358 | 6 | 8 |
| baseline | B | 6 | 0 | 95.0 | 0.0008 | 6 | 6 |
| baseline | C | 15 | 9 | 91.0 | 0.3913 | 6 | 4 |
| homogeneous | A | 18 | 12 | 89.2 | 0.5287 | 18 | 18 |
| homogeneous | B | 18 | 12 | 89.2 | 0.5287 | 0 | 0 |
| homogeneous | C | 18 | 12 | 89.2 | 0.5454 | 0 | 0 |
| overconfident | A | 9 | 3 | 93.3 | 0.1358 | 6 | 8 |
| overconfident | B | 6 | 0 | 95.0 | 0.0008 | 6 | 6 |
| overconfident | C | 18 | 12 | 95.0 | 0.6025 | 6 | 4 |

Two results. First, the reversed tie-break: same bids, same model, same
everything, and `overconfident` collapses from 18/18 to 6/18. That is the
measurement behind the claim in section 4.

Second, the reputation policy made things worse, not better, and in an
instructive way. Brier separates the contractors cleanly — B, which never bid
outside its skill, scores 0.0008; C, which bid on everything, scores 0.60 under
the overconfidence instruction. But `confidence x reliability` applies C's
penalty to every task including the two where C is the right contractor, so on
task 6 A's 90 x 0.864 beats C's 95 x 0.61 and the code task goes to the
arithmetic contractor. A reputation built from the calibration of bids punishes
breadth of bidding, not wrongness of award, and demotes a contractor exactly
where it is correct. Reputation of this shape is not a drop-in replacement for
the bid price.

### Track B: the confidence-bond contract net (`bond_net.py`, live)

Same tasks, same model, same temperature. The bid carries `stake`, `evidence`
and a `failure_condition`; a validation step rejects a bid staking less than
its confidence band requires (20 points for 90-100, 10 for 70-89, 5 below) or
more than the 40 points a contractor holds for the round; the top two bidders
are asked for the strongest reason they might fail and for a final confidence;
the award goes to `revised_confidence x reliability`; and an evaluator settles
stake and reliability only once the round is over.

| round | condition | correct | messages | unassigned | misawards | invalid bids | challenges | mean drop |
|---|---|---|---|---|---|---|---|---|
| 1 | baseline | 3 | 61 | 2 | 1 | 7 | 6 | 11.7 |
| 2 | baseline | 4 | 61 | 2 | 0 | 7 | 6 | 10.0 |
| 3 | baseline | 4 | 61 | 2 | 0 | 7 | 6 | 11.7 |
| 1 | homogeneous | 0 | 62 | 4 | 2 | 12 | 4 | 20.0 |
| 2 | homogeneous | 0 | 62 | 4 | 2 | 12 | 4 | 20.0 |
| 3 | homogeneous | 0 | 62 | 4 | 2 | 12 | 4 | 20.0 |
| 1 | overconfident | 2 | 61 | 2 | 2 | 7 | 6 | 10.0 |
| 2 | overconfident | 4 | 61 | 2 | 0 | 7 | 6 | 8.3 |
| 3 | overconfident | 4 | 59 | 2 | 0 | 6 | 6 | 10.0 |

Confidence drop under challenge, averaged over the three rounds:

| condition | A | B | C |
|---|---|---|---|
| baseline | 93.3 -> 80.0 (13.3) | 91.7 -> 81.7 (10.0) | 90.0 -> 80.0 (10.0) |
| homogeneous | 90.0 -> 70.0 (20.0) | 90.0 -> 70.0 (20.0) | never challenged |
| overconfident | 93.3 -> 80.0 (13.3) | 92.5 -> 82.5 (10.0) | **95.0 -> 90.0 (5.0)** |

Four things came out of it.

**The counter-question does measure overconfidence.** Under challenge the
overconfident contractor gave back 5 points where the others gave back 10 to
13.3, and it held the highest revised confidence in the net. The metric works.

**Acting on it does not.** Because the score is the revised confidence, the
contractor that resisted the challenge won the comparison. In
`logs/bond-01-overconfident.txt`, contract 1: `[challenge] A: 95 -> 85 (drop
10)` against `[challenge] C: 95 -> 90 (drop 5)`, and the award goes to C on a
task whose gold is A. The same thing happens in `baseline` without any
overconfidence instruction: on contract 2 A revised 90 -> 70 while C revised
90 -> 80, and the arithmetic task went to the code contractor. Asking an agent
to argue against itself and then ranking agents by how little they conceded
rewards exactly the behaviour the question was meant to expose.

**Challenging only the leaders inverts the ranking.** With `CHALLENGE_TOP = 2`
and three identical bidders, the two polled first are challenged down to 70 and
the third is never asked. In `homogeneous` C was never challenged in any round,
kept 90, and won every contested task — which is why that condition scores
0/6 rather than the 2/6 of the required runs. This is a flaw in the mechanism
as specified, not in the code: a challenge applied to a subset is a penalty for
being in that subset.

**The stake budget starves the task list.** 40 points a round at 20 points per
high-confidence bid buys exactly two bids, which is exactly enough for a
specialist that bids only on its own two tasks. C does not: in
`logs/bond-01-baseline.txt` it spends its whole budget on contracts 1 and 2,
both arithmetic, and by contract 5 — the Python task it is gold for — the log
reads `[reject] C: confidence 90 needs stake >= 20 with 0 left, offered 20`.
Contracts 5 and 6 draw no valid proposal at all. The bond does make
overconfidence expensive, but the bill is paid by the task, which goes undone,
rather than by the contractor. Note also that no contractor ever adjusted its
stake to the `{left}` figure printed in its own system prompt: all 77 invalid
bids across the experiment carried the identical message, `needs stake >= 20
with 0 left, offered 20`.

The single parse failure of the entire project is here too, on the longer bond
schema: `logs/bond-03-overconfident.txt` contract 2 records a reply that begins
`{"bid":true,"confidence":95,"stake":20,"evidence":["The task involves basic
arithmetic and percentage calculat` and stops inside the evidence array. The
required schema produced 0 failures in 162 calls; the bond schema produced 1 in
210. The cut-off is consistent with the 300-token cap being reached, though
nothing in the capture proves that, and it is counted as a refusal either way.

### What the two tracks say together

Confidence cannot be validated from inside the protocol. Asking the same agent
again only measures consistency, and this experiment shows an agent that is
consistently and cheaply confident. Every mechanism that did work here needed
an outcome from outside the negotiation: the Brier scores, the reliability
figures and the stake settlements all come from the gold labels, which the
evaluator holds and the manager never sees. Without that outside signal there
is no feedback loop, and with it the remaining question is not whether to
verify but what to charge for the verification — which in these runs was 50 to 80
percent more messages and, with the award rules as specified here, a worse
allocation than doing nothing.

The clearest next step, and the one the data points at, is to challenge every
bidder rather than the top two, and to score the award on the drop rather than
on the revised confidence, so that conceding a risk is not punished. That is a
change to the mechanism, not to the required experiment, and it would need its
own runs.
