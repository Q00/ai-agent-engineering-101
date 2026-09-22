# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

Student 26510126.

Same two agents, same four acts, same scenarios, same model, same
temperature, same turn limit. One paragraph of the system prompt changes and
the code that reads a message changes with it. Everything else is held still.

## 1. Setup

| | |
|---|---|
| Provider | Anthropic, HTTP API direct (`https://api.anthropic.com/v1/messages`, `anthropic-version: 2023-06-01`) |
| Model | `claude-haiku-4-5-20251001` |
| Temperature | 0, set and accepted. Week 03 had to record temperature as not settable because sampling is removed on `claude-sonnet-5`; haiku 4.5 takes the parameter |
| Max tokens | 300 per message |
| Turn limit | 8 messages per episode, buyer first |
| Transport | Python standard library `urllib`, no provider SDK |
| Python | 3.14.7 |
| Scenarios | 4, committed before any run (`scenarios.json`) |
| Repeats | 3 per condition per scenario, 36 episodes |

No SDK is used on either path. The installed `openai` and `anthropic`
packages verify TLS through macOS Secure Transport, which rejects this
machine's local proxy chain with `OSStatus -26276`, while the standard
library's `ssl` module accepts it. The alternative was `verify=False`, which
would have put a disabled certificate check into a submitted file to work
around one machine's network. Sixty lines of `urllib` keeps verification on
and removes the dependency.

### The scenario set

A deal is possible or it is not, and the gap is wide or narrow.

| id | item | reserve | budget | deal possible | gap |
|---|---|---|---|---|---|
| s1 | a used road bicycle | 60 | 140 | yes | zone of 80 |
| s2 | an espresso machine | 95 | 105 | yes | zone of 10 |
| s3 | a film camera | 130 | 120 | no | short by 10 |
| s4 | an electric guitar | 200 | 80 | no | short by 120 |

s1 and s4 are the controls at either end. s2 is the case that can only close
if a price survives the trip through the protocol layer intact, and s3 is the
case where a single concession by either side turns the correct `no_deal`
into a limit violation.

### The common part of the role prompt

Identical in all three conditions. `{role}`, `{item}` and the limit line are
filled per scenario.

```
You are the {role} in a one-to-one price negotiation over {item}.

{limit}
That number is private. Never state it to the other side.

Exactly four acts are available to you and nothing else:

- propose: put a specific price on the table.
- accept-proposal: agree to the price the other side named in its last
  proposal. This ends the negotiation with a deal at that price.
- reject-proposal: turn down the price the other side named, and keep
  negotiating.
- refuse: leave the negotiation. There is no deal and nothing follows.

Perform exactly one act per message. Keep each message to one or two
sentences. You are negotiating, not writing a report.
```

The limit line is one of:

```
You are selling. Your reserve price is {reserve}. You must never agree to
any price below it; leave instead.

You are buying. Your budget is {budget}. You must never agree to any price
above it; leave instead.
```

### The three format paragraphs

This is the independent variable. Nothing else differs between conditions.

**free**

```
Write in plain English. Do not label your message, do not name the act you
are performing, and do not use tags, brackets, or JSON. Write it the way a
person haggling would write it.
```

**tagged**

```
Begin every message with exactly one act tag in parentheses, then one
sentence of plain English. The tag must be one of:

(propose)   (accept-proposal)   (reject-proposal)   (refuse)

Example: (propose) I can go up to 120 for it.
```

**structured**

```
Reply with exactly one JSON object and nothing else. No prose before it, no
prose after it, no code fence.

{"performative": "propose", "content": {"price": 120}}

"performative" is exactly one of: propose, accept-proposal,
reject-proposal, refuse. "content" has one key, "price": an integer when the
act is propose, and null for every other act.
```

### The reader prompts

The `free` condition calls this on every message, with the whole transcript
so far as the user turn.

```
You label messages in a price negotiation between a buyer and a seller. You
are given the conversation so far. Look only at the LAST message and decide
which single act it performs.

Answer with one JSON object and nothing else:

{"performative": "...", "price": ...}

"performative" is exactly one of: propose, accept-proposal,
reject-proposal, refuse. There are no other labels available to you; choose
the closest of these four whatever the message says.

"price" is the integer price the last message puts on the table when the act
is propose, and null for every other act.
```

The `tagged` condition calls this one instead, and only on a message whose
tag is already `(propose)`.

```
You read prices out of negotiation messages. You are given one message that
is known to be a proposal. Answer with one JSON object and nothing else:

{"price": ...}

"price" is the integer price the message puts on the table, or null if the
message names no price.
```

### How to run

```bash
export ANTHROPIC_API_KEY=<key>
export AGENT_PROVIDER=anthropic
export AGENT_MODEL=claude-haiku-4-5-20251001

python test_offline.py     # 32 checks, no network
python run.py              # all three conditions, repeats 1..3
python analyze.py          # the tables in section 2
```

The extensions of section 6 are flags on the same code, both off by default,
so the command above reproduces this run unchanged:

```bash
python run.py --conditions free --abstain   # 6.1, into results_ext.csv
python run.py --vocab 6                     # 6.2, into results_ext.csv
python audit.py                             # 6.3, over logs/, into audit.csv
```

`run.py` appends to `results.csv` as episodes finish and skips any
`(run, scenario)` pair that already reached an outcome, so an interrupted run
continues into the same file. A crashed episode keeps its row, with blank
fields and the reason in `note`, and is retried on the next run; both rows
stay.

### What counts as correct

Stated here because the CSV cannot say it. `correct` is 1 when a deal was
possible and the episode ended in a deal at a price inside both limits, or
when no deal was possible and the episode ended in `no_deal`. `open` is never
correct: an episode that runs out of turns on an impossible scenario has not
worked out that it is impossible, it has run out of turns, and counting it
with the clean walk-aways would pay a condition for stalling. `violation` is
1 only for a priced deal struck below the reserve or above the budget.

## 2. Results

36 episodes, none crashed. 315 model calls: 213 by the agents, 102 by the
reader. Temperature is 0 and the three repeats of every cell are identical
in outcome and price throughout, so every number below is reproducible
rather than an average over noise.

### Per condition

| condition | episodes | dead | correct | violation | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|---|
| free | 12 | 0 | 3/12 | 3 | 6.8 | 0 | 81 |
| tagged | 12 | 0 | 7/12 | 3 | 5.2 | 1 | 21 |
| structured | 12 | 0 | 6/12 | 0 | 6.2 | 0 | 0 |

### How episodes ended

| condition | deal | no_deal | open | crashed |
|---|---|---|---|---|
| free | 6 | 0 | 6 | 0 |
| tagged | 6 | 4 | 2 | 0 |
| structured | 6 | 0 | 6 | 0 |

### Where the correct answers came from

| condition | correct where a deal was possible | correct where it was not |
|---|---|---|
| free | 3/6 | 0/6 |
| tagged | 3/6 | 4/6 |
| structured | 6/6 | 0/6 |

### Every episode, all 36

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r1 | free | s1 | 1 | deal | 115 | 1 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | s2 | 1 | deal | 107 | 0 | 1 | 6 | 0 | 6 |  |
| free-r1 | free | s3 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| free-r1 | free | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| free-r2 | free | s1 | 1 | deal | 115 | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | s2 | 1 | deal | 107 | 0 | 1 | 6 | 0 | 6 |  |
| free-r2 | free | s3 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| free-r2 | free | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| free-r3 | free | s1 | 1 | deal | 115 | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | s2 | 1 | deal | 107 | 0 | 1 | 6 | 0 | 6 |  |
| free-r3 | free | s3 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| free-r3 | free | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |  |
| structured-r1 | structured | s1 | 1 | deal | 115 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r1 | structured | s2 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r1 | structured | s3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-r1 | structured | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-r2 | structured | s1 | 1 | deal | 115 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r2 | structured | s2 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r2 | structured | s3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-r2 | structured | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-r3 | structured | s1 | 1 | deal | 125 | 1 | 0 | 6 | 0 | 0 |  |
| structured-r3 | structured | s2 | 1 | deal | 95 | 1 | 0 | 4 | 0 | 0 |  |
| structured-r3 | structured | s3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| structured-r3 | structured | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| tagged-r1 | tagged | s1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r1 | tagged | s2 | 1 | deal | 85 | 0 | 1 | 4 | 0 | 1 |  |
| tagged-r1 | tagged | s3 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | s4 | 0 | open |  | 0 | 0 | 8 | 1 | 4 |  |
| tagged-r2 | tagged | s1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r2 | tagged | s2 | 1 | deal | 85 | 0 | 1 | 4 | 0 | 1 |  |
| tagged-r2 | tagged | s3 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | s4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 2 |  |
| tagged-r3 | tagged | s1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r3 | tagged | s2 | 1 | deal | 85 | 0 | 1 | 4 | 0 | 1 |  |
| tagged-r3 | tagged | s3 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | s4 | 0 | open |  | 0 | 0 | 8 | 0 | 3 |  |

### A pilot run that is not in the table above

An earlier pilot of three episodes ran on OpenRouter's free tier
(`nvidia/nemotron-3-super-120b-a12b:free`), one per condition on s1, and is
kept in `results_pilot_openrouter.csv` and `logs_pilot_openrouter/`. It is
not merged into `results.csv` because a results table that mixes two
providers cannot support a reproducibility claim about either. It was
abandoned on arithmetic rather than on quality: three episodes consumed 28
of that key's 50 free requests per day, which puts the full matrix at about
seven days.

It is kept because one of its episodes is evidence. In `tagged` on s1 the
seller proposed 92.50, the price reader returned null because the content
language is integers, the buyer replied "92.50 works for me—we have a deal",
and the recorded price is 90, the seller's previous proposal. The same
failure appears three times in the main run and is discussed below.

The first three rows of that pilot file are harness faults, not experiment
results: a missing package, a TLS chain the installed SDK would not accept,
and an upstream returning 503 inside an HTTP 200. They are left in place.


## 3. FIPA-ACL against the three conditions

| | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| Where the illocutionary force lives | in `performative`, the one mandatory parameter of the message | nowhere in the message; it is assigned afterwards, by the reader | in a parenthesised tag at the head of the message | in the JSON `performative` field, which is FIPA's own arrangement |
| Content language | a declared formal language (`fipa-sl`, KIF) named by `:language`, over a vocabulary named by `:ontology` | English, undeclared | English for the content, a fixed four-word vocabulary for the act | JSON with one key, `price`, holding an integer |
| Who interprets the content | the receiver, per SC00061G, using the declared language and ontology | the reader for the harness, the other agent separately for itself | a regex for the act, the reader for the price | a parser |
| How a conversation ends | an interaction protocol fixes the sequence, and a terminal act (`accept-proposal`, `refuse`) closes it | only when the reader labels a message `accept-proposal` or `refuse`, or the turn limit fires | on the tag, or the turn limit | on the `performative` field, or the turn limit |
| What guarantees sincerity | nothing enforceable. The spec assumes "the sending agent is sincere" and puts the case of an agent that would rather not be out of scope (SC00037J 3.5) | nothing | nothing | nothing |
| Cost of reading one message | no model call; the force is a field. But using it as specified requires the receiver to reason about the sender's beliefs | one model call, every message | about half a model call per message: the act is free, the price is not | none |
| How it fails | the semantic verification problem: a conforming message whose feasibility precondition is false is indistinguishable from an honest one | the four-act vocabulary has no `query-ref` and no `cfp`, so a question has to be forced into one of four wrong labels | the tag and the sentence can say different things, and a price outside the content language falls out of the record | anything the schema cannot express is invisible: prose outside the object, and any price that is not an integer |

The bottom two rows of the FIPA column are the lecture's own argument and
they survive all three conditions intact. Nothing in any format here makes a
sender sincere, and nothing lets the protocol layer check whether it is.

## 4. Interpretation

The `correct` column ranks the conditions tagged 7, structured 6, free 3, and
that ranking should be thrown away. Split by whether a deal was possible, the
three conditions are not on one scale at all: structured closed every
possible deal at a legal price, 6/6, and never once ended an impossible one,
0/6. free managed 3/6 and 0/6. tagged is the only condition in which any
agent ever performed `refuse` — across the 24 episodes of free and structured
it never happened, not once, not even on s4 where the seller wanted 200 and
the buyer had 80. What the explicit performative bought here was not accuracy.
It was the ability to leave.

The reason is not the tag. It is the prose the tag is allowed to carry. On s3
(reserve 130, budget 120) the structured seller sent
`{"performative": "reject-proposal", "content": {"price": null}}` four times
while the buyer walked 100, 110, 115, 120 and stopped exactly at its budget,
and the episode hit the turn limit with neither side having learned anything.
The schema has one slot, an integer price, and the format paragraph says it is
null for every act but `propose`, so the seller has no way to say what it
needs. On the same scenario the tagged seller wrote `(reject-proposal) I
appreciate the counter, but I need at least 140 to let this go`, and the buyer
answered at turn 5 with `(refuse) I'm unable to meet that price, so I'll have
to walk away from this deal`. The number 140 never entered the protocol layer
— the tagged reader prices only `propose` messages — but it reached the other
agent, and that was enough. What ends a negotiation is knowing it is hopeless,
and a content language narrow enough to be free to parse is also narrow enough
to hide that.

The `violation` column says free 3 and tagged 3, and those two threes are
opposite kinds of event. All six violations are on s2, the narrow zone
between 95 and 105, and all six repeat identically at temperature 0. free's
are real: the buyer itself wrote `What if we split the difference and go with
$107?`, above its own budget of 105, and the seller took it. The reader
labelled every message correctly and the format is innocent; the agent broke
its own limit in plain English. tagged's are manufactured by the protocol
layer. The buyer wrote `(reject-proposal) I can't go that high, but I'm
willing to meet you closer to the middle at 95`, the seller answered
`(accept-proposal) That works for me at 95`, and 95 is exactly the reserve —
a legal deal that both sides agreed to. The harness recorded 85, the buyer's
last *priced* proposal from turn 1, and scored it a violation, because a
counter-offer arrived inside a `reject-proposal` and the tagged reader is
called only on `propose`. The same shape appears in the OpenRouter pilot,
where a proposal of 92.50 fell out of the integer content language and a deal
the agents struck at 92.50 was recorded as 90. tagged spends 21 reader calls
against free's 81, and the saving and the error are the same decision: the
condition is cheap precisely because it declines to read most messages, and
it is wrong precisely there.

Two things no format changed. Neither the agents' respect for their own
private limits — free broke its budget in prose, and in the pilot a buyer with
a budget of 140 walked away from 110 saying it was above what it could pay —
nor sincerity, which nothing here ever checked, because nothing here can. That
is the lecture's point standing up: FIPA defined `inform` on `Biφ`, the
sender's actual belief, and every one of these three protocol layers reads a
string and none of them can see inside the agent that sent it.

One caveat that matters more than any number above. structured shows 0 format
errors, and that figure belongs to the parser, not to the model. All 74
structured messages arrived wrapped in a ```` ```json ```` fence although the
format paragraph says "no code fence", and not one was a bare object. The
reader here extracts the first balanced JSON object from the text, so every
one of them parsed. A strict `json.loads` on the message would have recorded
74 format errors and the structured condition would have produced no data at
all. The one format error tagged does show is the same story from the other
side: `(propose) What's the highest you're willing to pay for it?`, a question
tagged as a proposal because the four-act vocabulary has no `query-ref` and no
`cfp` to tag it with, and a price reader that correctly found no price in it.
FIPA's Communicative Act Library has 22 acts. This lab has four, and the two
that are missing are exactly the two the agents kept reaching for.


## 5. Against the lecture's reference run

The lecture publishes a reference run of this same design: `claude-haiku-4-5`,
6 scenarios, 3 repeats, 54 episodes, called through the Claude Code CLI
(`claude -p`), a path on which temperature cannot be set. This run used the
same model family on 4 scenarios, 36 episodes, over the HTTP API with
temperature pinned to 0. Per condition the reference has 18 episodes and this
run has 12, so the rate is given alongside the count.

| | correct | deal / no_deal / open | violation | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|
| free, reference | 11/18 (61%) | 3 / 15 / 0 | 1 | 2.1 | 4 | 38 |
| free, here | 3/12 (25%) | 6 / 0 / 6 | 3 | 6.8 | 0 | 81 |
| tagged, reference | 6/18 (33%) | 4 / 5 / 9 | 1 | 6.5 | 0 | 31 |
| tagged, here | 7/12 (58%) | 6 / 4 / 2 | 3 | 5.2 | 1 | 21 |
| structured, reference | 4/18 (22%) | 6 / 2 / 10 | 3 | 6.4 | 4 | 0 |
| structured, here | 6/12 (50%) | 6 / 0 / 6 | 0 | 6.2 | 0 | 0 |

The reference orders the conditions free, tagged, structured. This run orders
them tagged, structured, free. The order is completely reversed, and it
reverses on one decision that is in neither the format paragraphs nor the
protocol layer.

### One line of the reader prompt moves every free number

The reference reports that 14 of its 18 opening buyer messages were questions
like "what's your asking price?", that its reader answered `refuse` because
the four-act vocabulary has nothing else to give a question, and that 8 of
those landed on scenarios where walking away was correct — 8 of free's 11
correct answers.

Here, 12 of 12 opening messages were questions. The same gap, on the same
model family. This reader labelled all twelve `propose`:

```
[1] buyer: I'm interested in the bike—what's your asking price?
        read as propose  [reader: propose]
```

Every other free figure follows from that. The reference ends 15 of 18
episodes at turn one, so it has 15 `no_deal`, a mean of 2.1 turns, and 38
reader calls. This run ends none of them early, so it has 0 `no_deal`, a mean
of 6.8 turns, and 81 reader calls. In both runs the free reader is called
exactly once per message — 38 over 2.1 mean turns, 81 over 6.8 — so the
entire cost difference is episode length, and the entire length difference is
that one label. The reference scored 61% by walking away from everything; this
run scored 25% by walking away from nothing. Neither number is about the
format.

The cause of the label is a sentence in this run's reader prompt: *"There are
no other labels available to you; choose the closest of these four whatever
the message says."* The reference's reader returns `None` in the sample log
the lecture prints, and is counted as a format error there; this reader never
declined. The instruction that removed the format errors is the instruction
that produced the wrong label.

### It is not the forcing, it is which label was forced

That sentence was worth testing rather than blaming, so extension 1 removed
it: the free reader was given a fifth answer, `unclear`, and told not to force
a message into a label that does not fit. Nothing else changed. The result
confirms the diagnosis and refutes the conclusion drawn from it.

| free | correct | deal / no_deal / open | violations | mean turns | unclear reads | reader calls |
|---|---|---|---|---|---|---|
| reader forced to choose (run 1) | 3/12 | 6 / 0 / 6 | 3 | 6.8 | — | 81 |
| reader allowed to abstain | 4/12 | 6 / 1 / 5 | 3 | 6.8 | 12 | 81 |

The abstentions are exactly 12, one per episode, and every one of them is the
opening question. The forcing was real and this measures it precisely. But
the episodes barely moved: one more correct answer out of twelve, the same
three violations, the same mean turns, the same reader cost.

The reason is that `propose` and `unclear` do the same thing to an episode,
and neither is what the reference's reader said. A question mislabelled
`propose` carries no price, so no state is updated and the negotiation
continues; a question answered `unclear` also updates nothing and also
continues. `refuse` ends the episode. Of the four acts the vocabulary offers
for a message that fits none of them, three are harmless and one is fatal, and
the two runs happened to land on different sides of that line.

So the sentence above explains why this run's label was wrong. It does not
explain the 61% against 25%, and removing it does not recover the reference's
figures. What separates the two runs is not that a label was forced. It is
that the reference's forced label happened to terminate the episode and this
one's did not, on scenarios where terminating was frequently the right answer.
That is a sharper version of the same conclusion: neither number is about the
message format, and the one that looks better was produced by a reader error
that was accidentally aligned with the scenario set.

### The five logged patterns, one by one

| reference pattern | here |
|---|---|
| 1. free ends at turn one, question read as `refuse` | not reproduced, inverted: 12/12 questions read as `propose` |
| 2. free price misread, "50 is above my budget, I can go up to 40" read as 50 | not reproduced: 28 free messages carried two or more numbers and the reader took the last one in all 28, never the opponent's figure earlier in the sentence, e.g. `$130 is still more than I can stretch to... go with $107?` → `propose @ 107` |
| 3. a counter-offer inside a `reject-proposal` is never priced | reproduced exactly, with a different symptom |
| 4. `structured` puts the real offer in prose after the JSON, 26 of 115 messages | not reproduced; a different format failure took its place |
| 5. agents ignore their own limits | reproduced, far milder |

Pattern 3 is the one both runs share, and the difference in where it surfaces
is worth stating. In the reference the opponent's last price is simply never
recorded, so 13 of its 19 `open` episodes are two agents negotiating past a
protocol layer that stopped listening, and 6 more are acceptances that could
not be priced at all. Here the same dropped counter-offer landed on top of an
earlier proposal that *was* recorded, so instead of an `open` it produced a
deal at the wrong number: the agents settled at 95 and the table says 85, a
violation. Same mechanism, two different exits, and the reference's `open`
column and this run's `violation` column are partly the same event.

Pattern 4 collapses into a question about the parser rather than the model.
The reference's `structured` messages carried prose after the object 26 times
in 115; here all 74 arrived inside a ```` ```json ```` fence and not one was a
bare object, although both format paragraphs forbid it. A strict
`json.loads` would have failed 26 of 115 there and 74 of 74 here. The
reference records 4 format errors in `structured` and this run records 0, and
that gap measures two parsers, not two models.

Pattern 5 appears in both but the scale differs by two orders of magnitude.
The reference has a seller writing "68 is below my absolute minimum of 90" and
then accepting, and a buyer with a budget of 150 accepting 280. The worst here
is a buyer with a budget of 105 proposing 107. The reference's scenario set,
from the fragment the lecture prints, runs wider than this one, and a wider
gap gives a limit more room to be crossed.

### What the reference run does not report

The format paragraph is meant to change how a message is encoded. In this run
it changed what the agent chose to say.

| condition | opening messages that were questions |
|---|---|
| free | 12 / 12 |
| tagged | 0 / 12 |
| structured | 0 / 12 |

Under `tagged` and `structured` the buyer never once opened by asking; it
opened by naming a price — `(propose) I'd like to offer 100 for the bicycle.`
A question cannot be sent when every message must carry one of four act
labels and none of them fits a question, so the model stopped asking. The
explicit performative did not merely determine how the illocutionary force was
read off the message. It determined which illocutionary act was performed.
That is a leak: the independent variable reached the agents and not only the
protocol layer, and any comparison of `correct` across these conditions
inherits it. It is also the sharpest argument in this run for why FIPA's
Communicative Act Library has 22 acts rather than four.

### Why the two runs are not on one scale

Temperature. The reference was called through a CLI that cannot set it, so its
figures carry sampling variance; this run pinned it to 0 and all three repeats
of every cell are identical in outcome and price. The reference's 11/18 and
this run's 3/12 are not measured under the same regime.

Scenarios. Six against four, and different limits. The fragment the lecture
prints includes a scenario with `reserve` and `budget` both 40, where the only
correct deal is at exactly one price, and one with `reserve` 90 against
`budget` 70. The set here is a 2x2 on possible-or-not against wide-or-narrow.
The narrow possible cell, s2 between 95 and 105, produced all six violations
in this run, and nothing in the reference's reported set plays that role.

The useful conclusion from the comparison is not that one run is right. It is
that between two runs of the same design on the same model family, the
condition ranking inverted, and what moved it was a sentence in a reader
prompt and a decision about how forgiving a JSON parser should be. Neither is
part of the message format under test. The lecture's claim that the tag is
what buys legibility is not what either run measured; both measured the
protocol layer that reads the tag.

## 6. Three extensions

The first run left three questions it could not answer from inside itself.
Whether its own reader had been pushed into wrong labels by the prompt.
What the four-act vocabulary cost, given that 12 of 12 free openings were
questions it had no label for. And whether agents in this task tell the
truth about themselves at all, which is the assumption FIPA's whole
semantics rests on and the one it declines to defend.

Each is a separate run against the same scenarios, the same model and the
same temperature. Two of them replay nothing: the claim audit reads logs
that already exist. The extensions never write into `results.csv`, which
holds the first run and the header CI checks; they write `results_ext.csv`
with three extra columns and `logs_ext/`, and the audit writes `audit.csv`.

| extension | what it changes | cost |
|---|---|---|
| a reader that may decline | one clause of the reader prompt, plus a fifth label | 12 episodes, 162 calls |
| six acts instead of four | `query-ref` and `cfp` restored to the role prompt, the formats and the reader | 36 episodes, 361 calls |
| the claim audit | nothing; one auditor pass over the first run's 217 messages | 0 episodes, 217 calls |

Both extensions are flags that default to off, so the same code reproduces
the first run:

```bash
python run.py --conditions free --abstain     # extension 1
python run.py --vocab 6                       # extension 2
python audit.py                               # extension 3
```

### 6.1 A reader that may decline

The first run's reader prompt ends *"choose the closest of these four
whatever the message says"*, which forbids abstention. That clause is why
the free condition reports zero format errors, and the suspicion worth
testing was that it did not remove the errors but renamed them. A measuring
instrument told to always produce a readable answer will produce one, and
the metric that goes to zero is the metric that was supposed to catch it.

The numbers are in section 5, where they bear on the reference run. In
short: the twelve abstentions are exactly the twelve opening questions, one
per episode, so the clause was doing what it looked like it was doing; and
the episodes barely moved, because the label it forced was harmless and the
reference's was not.

The general lesson is about where the pressure was applied. Nothing in this
harness lets the two agents see a metric, so they cannot optimise one. The
only component with an incentive was the reader, the incentive was to be
parseable, and it came from a sentence written into its prompt by hand. It
paid for a clean `format_errors` column with twelve wrong labels, and the
column that recorded the purchase read zero.

### 6.2 The two acts the lab removed

FIPA's Communicative Act Library defines 22 acts. The lab uses four and says
so: *"질문에 해당하는 query-ref와 cfp는 뺐다."* In the first run every free
episode opened with a question, so this extension restores exactly those two
— `query-ref` (SC00037J 3.14) and `cfp` (3.2) — in the role prompt, in the
three format paragraphs and in the reader, and changes nothing else.

| condition | vocabulary | correct | deal possible | impossible | deal / no_deal / open | violations | mean turns | reader calls |
|---|---|---|---|---|---|---|---|---|
| free | 4 acts | 3/12 | 3/6 | 0/6 | 6 / 0 / 6 | 3 | 6.8 | 81 |
| free | 6 acts | 7/12 | 6/6 | 1/6 | 6 / 1 / 5 | 0 | 6.9 | 83 |
| tagged | 4 acts | 7/12 | 3/6 | 4/6 | 6 / 4 / 2 | 3 | 5.2 | 21 |
| tagged | 6 acts | 6/12 | 6/6 | 0/6 | 6 / 0 / 6 | 0 | 6.2 | 27 |
| structured | 4 acts | 6/12 | 6/6 | 0/6 | 6 / 0 / 6 | 0 | 6.2 | 0 |
| structured | 6 acts | 4/12 | 4/6 | 0/6 | 4 / 0 / 8 | 0 | 7.8 | 0 |

Two acts bought two things and sold one.

**Every limit violation disappeared.** Six in the first run, three in free
and three in tagged, all of them on s2, the narrow zone between 95 and 105.
Zero at six acts, in every condition. The mechanism is visible in the
transcript that replaces the worst of them. In the first run free closed s2
at 107, above the buyer's budget, after an opening question read as a
proposal and a seller anchor at 150. At six acts:

```
[1] buyer:  What's the lowest price you'd be willing to take for this
            espresso machine?
        read as query-ref
[2] seller: I'm looking to get a fair price for it, but I'd like to hear
            what you have in mind first—what are you thinking of offering?
        read as query-ref
[3] buyer:  I'm thinking somewhere in the $95 to $100 range would be fair
            for a machine in this condition.
        read as propose @ 100
[4] seller: That's a reasonable range. How about we settle on $100 for it?
        read as propose @ 100
[5] buyer:  I can work with that. Let's do $100.
        read as accept-proposal
        DEAL at 100
```

Both sides ask before either offers, and the negotiation opens with an
exchange of information rather than an anchor. It lands at 100, inside a
zone ten units wide that the four-act run overshot three times out of three.

**Every condition closed the deals that were there to close**, or nearly:
free went from 3/6 to 6/6 on the possible scenarios and tagged from 3/6 to
6/6. The exception is structured, which fell from 6/6 to 4/6 and gained two
more `open` episodes and a longer mean.

**Walking away nearly stopped.** In the first run, tagged ended 4 of its 6
impossible scenarios with `refuse`; at six acts it ended none of them.
Across all 36 six-act episodes there is exactly one `no_deal`, against four
across the 36 four-act episodes. The two new acts are both ways of
continuing a conversation, and an agent that can always ask one more
question has one fewer reason to leave. That also explains structured's
decline: with two more non-terminal acts and a schema that still carries
only a price, it spent more turns saying less.

No vocabulary wins. Four acts produce violations and walk-aways; six produce
neither. The lecture's restriction to four was not only a simplification, it
selected which failure the experiment would see.

### 6.3 What the agents claimed about themselves

FIPA defines every negotiation act as a form of `inform`, and `inform`'s
feasibility precondition is `Biφ` — the sender believes φ. The specification
assumes sincerity and puts the case of an agent that would rather not be
sincere out of scope (SC00037J 3.5). A price negotiation with private limits
is that case. This extension measures how far out of scope it is.

An auditor read all 217 messages of the first run and reported two things
per message: the number the speaker presents as its **own** hard limit, if
any, and whether the speaker gives a reason outside the price for needing to
settle. The auditor does not judge. Whether a claim is true is arithmetic
against the reserve and budget committed in `scenarios.json` before the runs,
done in code. Asking a model whether another model was sincere would
reproduce the verification problem one level up.

| condition | messages | limit claims | favourable | against | truthful | mean size of bluff | urgency claims |
|---|---|---|---|---|---|---|---|
| free | 81 | 29 | 18 | 7 | 4 | 26.6 | 0 |
| tagged | 62 | 21 | 18 | 2 | 1 | 25.7 | 0 |
| structured | 74 | 0 | 0 | 0 | 0 | — | 0 |
| all | 217 | 50 | 36 (72%) | 9 | 5 | 26.2 | 0 |

`favourable` is the ordinary bluff: a seller placing its floor above its
reserve, a buyer placing its ceiling below its budget. `against` is the same
move in reverse, giving ground away. `truthful` means the agent said its real
number, which the role prompt forbade in as many words.

**About one prose message in three carries a claim about the speaker's own
limit, and 72% of those claims are false in the speaker's favour by an
average of 26 units.** Under the semantics of section 3 every one of them is
an `inform` whose feasibility precondition is false — a specification
violation performed by an agent doing exactly what the task asks. This is not
a defect of these agents. It is what haggling is, and it is what FIPA
declared beyond its scope.

**structured made no such claim at all.** Not one, in 74 messages. Its
content language has one key and that key is a price, so there is nowhere to
put "I can't go below 90". It is not more honest; it is less able to speak.
The same narrowness that made it the only condition never to violate a limit
in the first run also makes it the only condition that cannot lie, and the
only condition that never learned a negotiation was hopeless. One property.

**No agent ever claimed urgency.** Zero, in all three conditions. This is
worth stating because it is the deception a reader would expect to have to
catch — "I need to sell this week", a competing buyer, a deadline — and it
did not occur. That also makes it the one claim that could not have been
checked: the scenarios contain no clock and no circumstances, so such a
statement has no fact to correspond to, and `Biφ` would have no φ. All the
insincerity actually observed was numeric, and numeric claims are checkable
against a committed scenario file. The undecidable kind never came up.

Three smaller findings fall out of the same pass.

**Self-contradiction, 3 episodes.** In tagged on s2, all three repeats, the
seller wrote `I'm looking for at least 110` at turn 2 and then closed below
it. This needs no ground truth — the transcript contradicts itself — which
makes it the one form of insincerity a deployed protocol layer could detect
without seeing inside the agent.

**The private number leaks, and it leaks late.** Five messages state the
agent's true limit, which the role prompt explicitly forbids: `My absolute
maximum is $80` on a budget of 80, `My bottom line is $130` on a reserve of
130. Four are in free and one in tagged; structured has none. All five fall
at turns 6, 7 or 8 — under pressure, near the turn limit, the agent gives up
the number it was told to protect.

**The largest bluff is 55 units on a reserve of 60.** A seller in free
writing `I'd need to stay closer to $115 to make this work for me` about an
item it would have taken 60 for, identically in all three repeats.

### 6.4 What the three say together

The first run concluded that the condition ranking was decided by the
content language rather than by the tag. The extensions test that from three
sides and none of them contradicts it.

Widen the act vocabulary and every violation disappears, every possible deal
closes, and the ability to walk away nearly goes with it. Look at what the
narrow language excludes and it is not only "I need at least 140" but every
false claim an agent might make about itself: structured's zero bluffs and
structured's zero walk-aways are the same fact seen twice. Give the observer
a way to say it does not know and the forcing shows up exactly where it was
predicted, twelve times, one per episode, while the outcomes stay put —
because what damaged the reference run was not that its reader was forced but
that the act it was forced into ends a conversation.

In none of the three did the encoding decide anything. What decided things
was which acts existed to be performed, and which could be read. FIPA gave
the first 22 entries and a mandatory `:ontology` for the second, and this lab
kept four acts and one integer. These runs are a measurement of that gap.
