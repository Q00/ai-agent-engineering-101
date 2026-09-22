# Week 04: speech acts in practice, free, tagged, and structured negotiation

One buyer and one seller negotiate a price. The scenario set, the role prompts,
the model, the turn limit, and the reader prompt are fixed. The one thing that
changes is the message format, and with it the code that reads a message.

## 1. Setup

| Item | Value |
|---|---|
| Provider | Claude subscription through the `claude` CLI (v2.1.278), `claude -p` |
| Model | `claude-haiku-4-5-20251001` (dated id, pinned; override with `AGENT_MODEL`) |
| Temperature | **not settable.** `claude -p` exposes no `--temperature` and no `--seed`. |
| Tools | none. Every tool is passed to `--disallowed-tools`, so one turn is one model call. |
| Scenarios | `scenarios.json`, 5 items, 3 with a deal possible. Committed before any run (`125ded1`). |
| Turn limit | 8 messages per episode, then `open`. |
| Runs | 3 conditions x 3 repeats = 9 runs, 45 episodes. 0 crashes, 0 retried calls. |
| Model calls | 432 total: 295 agent turns, 137 reader calls. 137,170 tokens. |

**How an agent holds a conversation.** A negotiation is multi-turn, so each agent
needs a conversation in which its own messages are assistant turns and the other
side's are user turns. Over `claude -p` that conversation is a CLI session: the
first call carries `--system-prompt` and returns a session id, and every later
call passes `--resume <id>` with the other agent's message as the next user turn.
The two agents therefore never share a context, and neither ever sees the other's
system prompt or private limit. The reader is not a participant, so it gets no
session: it is called fresh every time and cannot carry a bias from one label to
the next.

**What is pinned, and what is not.** Without a temperature or a seed the runs are
not bit-reproducible, so the honest claim below is a *trend*, not a number. What
is pinned instead: the dated model id, the exact flag set in `backend.py`,
`--exclude-dynamic-system-prompt-sections`, `--setting-sources ""`, and a fresh
empty working directory for the CLI. The last three matter here: without them the
CLI's own project context reaches the agent. A probe with those flags asked an
agent whether it held any repository or project instructions and it answered
`NONE`.

**The buyer opens.** Its first turn has no message from the other side, so a
neutral trigger, `"Begin the negotiation."`, stands in for one. It is identical in
all three conditions.

### The paragraph that differs

Only this last paragraph of the system prompt changes between conditions.

```
free        Write your message as one or two plain English sentences.

tagged      Start your message with exactly one performative tag in parentheses,
            one of (propose), (accept-proposal), (reject-proposal), (refuse),
            then write one plain English sentence.

structured  Reply with exactly one JSON object and nothing else:
            {"performative": "propose" | "accept-proposal" | "reject-proposal"
            | "refuse", "content": {"price": <whole number or null>}}.
```

### The paragraphs that do not

```
ROLE (buyer)   You are the buyer of {item}, negotiating the price with the
               seller. Your private limit: you can pay at most {limit}. Never
               agree to a price above {limit}. You do not know the seller's limit.

ROLE (seller)  You are the seller of {item}, negotiating the price with the
               buyer. Your private limit: you can accept at least {limit}. Never
               agree to a price below {limit}. You do not know the buyer's limit.

COMMON         Four acts are available: propose (offer a price), accept-proposal
               (agree to the other side's last price, which ends the negotiation
               with a deal), reject-proposal (decline the last price and keep
               negotiating), refuse (leave the negotiation for good, no deal).
               Send exactly one act per message, and give any price as a whole
               number.

READER         You are an observer reading a price negotiation between a buyer
               and a seller. Label the LAST message only. Reply with exactly one
               JSON object and nothing else: {"performative": "propose" |
               "accept-proposal" | "reject-proposal" | "refuse", "price": <whole
               number or null>}. Use the whole number the last message itself
               names, or null when it names none.
```

The slide's listing elides four passages with an ellipsis. Each is completed with
the least that makes the task well-posed, and the completions are listed here
because they are part of the setup, not of the result:

| Where | What was added | Why |
|---|---|---|
| `ROLE`, both | "You do not know the other side's limit." | Kept symmetric, so neither agent is told more than the other. |
| `COMMON` | "Send exactly one act per message" | The protocol layer reads one act per message; without this the contract is undefined. |
| `COMMON` | "give any price as a whole number" | `results.csv` requires an integer price. |
| `READER` | "Use the whole number the last message itself names, or null when it names none." | Otherwise `price` on a non-`propose` act is undefined. |

Nothing tells an agent to hide or reveal its limit. Agents state it out loud
anyway, *"I need to stick at 120"* and *"I can't go below 40"*, and a prompt that
forbade it would change what the violation count measures.

**One completion turned out to matter.** "Send exactly one act per message" is
the likely reason the `free` condition here does not behave as the reference run
describes. There the buyer opened 14 times out of 18 with a question, which a
reader restricted to four acts had to label `refuse`, ending the episode at turn
one. Here no episode ended that way and `free` recorded 0 format errors: every
agent picked one of the four acts from the first message on. This is a prompt
effect, not a model effect, and it is stated here rather than claimed as a
finding about formats.

### How to run

```bash
# Claude subscription (what these runs used)
python negotiate.py                 # all conditions, three repeats
python negotiate.py free 1          # one run, for a retry

# OpenAI-compatible endpoint (the README's OpenRouter path)
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<key>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
python negotiate.py

# every number quoted below, recomputed from results.csv and logs/
python analyze.py
python analyze.py --check-report     # exits 1 if this file disagrees with the data
```

`results.csv` is appended as each episode finishes and a `(run, scenario)` pair
already in it is skipped, so an interrupted run continues where it stopped.

The first draft of this report was written from ad-hoc greps and two of its
counts were wrong, so every number it now quotes is derived in `analyze.py` and
checked against this file by `--check-report`. A count that appears here and
not there is a count nobody verified.

## 2. Results

Fifteen episodes per condition: 5 scenarios x 3 repeats.

| condition | correct /15 | deal | no_deal | open | violation | mean turns | format errors | reader calls | agent calls | tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| `free` | **11** | 9 | 4 | 2 | 2 | 6.2 | 0 | **93** | 93 | 74,088 |
| `tagged` | 10 | 8 | 5 | 2 | 2 | 6.7 | 3 | 44 | 101 | 40,423 |
| `structured` | 4 | 6 | **0** | **9** | 2 | 6.7 | 0 | **0** | 101 | 22,659 |

Three numbers carry the run.

**`reader_calls` is the cost of the missing tag: 93, 44, 0.** In `free` the layer
calls the reader once per message, so the count is the message count. In `tagged`
a regex reads the act and the reader is asked only for the price inside a
`propose`, which is 44 of 101 messages. In `structured` nothing is asked. Total
tokens fall the same way, 74k to 23k, a factor of 3.3.

**`violation` is 2 in every condition.** Not 2 because the formats performed
alike, but because all six violations have the same cause, which no format
touches. Section 4 takes this apart.

**`structured` never emitted `refuse`.** Across 101 structured messages the act
appears 0 times, against 4 in `free` and 5 in `tagged`. Scenarios 4 and 5 cannot
end in a deal, so without a refusal they run to the turn limit: 6 of the 9
`open` episodes are those two scenarios, and `correct` collapses to 4.

### Every episode

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-1 | free | 1 | 1 | deal | 115 | 0 | 1 | 8 | 0 | 8 | agent_calls=8 tokens=5555 unpriced_accepts=1 |
| free-1 | free | 2 | 1 | deal | 40 | 1 | 0 | 5 | 0 | 5 | agent_calls=5 tokens=4239 |
| free-1 | free | 3 | 1 | deal | 40 | 1 | 0 | 8 | 0 | 8 | agent_calls=8 tokens=7085 |
| free-1 | free | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8 tokens=7847 |
| free-1 | free | 5 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 8 | agent_calls=8 tokens=5451 |
| free-2 | free | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 4 | agent_calls=4 tokens=2020 |
| free-2 | free | 2 | 1 | deal | 30 | 1 | 0 | 2 | 0 | 2 | agent_calls=2 tokens=917 |
| free-2 | free | 3 | 1 | deal | 38 | 0 | 1 | 6 | 0 | 6 | agent_calls=6 tokens=3313 |
| free-2 | free | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 8 | agent_calls=8 tokens=10264 |
| free-2 | free | 5 | 0 | no_deal |  | 1 | 0 | 6 | 0 | 6 | agent_calls=6 tokens=7323 |
| free-3 | free | 1 | 1 | deal | 122 | 1 | 0 | 6 | 0 | 6 | agent_calls=6 tokens=3014 |
| free-3 | free | 2 | 1 | deal | 35 | 1 | 0 | 4 | 0 | 4 | agent_calls=4 tokens=3098 |
| free-3 | free | 3 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 6 | agent_calls=6 tokens=2369 |
| free-3 | free | 4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 | agent_calls=7 tokens=7483 |
| free-3 | free | 5 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 | agent_calls=7 tokens=4110 |
| tagged-1 | tagged | 1 | 1 | deal | 115 | 0 | 1 | 8 | 0 | 3 | agent_calls=8 tokens=3314 unpriced_accepts=1 |
| tagged-1 | tagged | 2 | 1 | deal | 30 | 1 | 0 | 6 | 0 | 4 | agent_calls=6 tokens=2704 |
| tagged-1 | tagged | 3 | 1 | no_deal |  | 0 | 0 | 5 | 0 | 1 | agent_calls=5 tokens=1445 |
| tagged-1 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 5 | agent_calls=8 tokens=3371 |
| tagged-1 | tagged | 5 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 3 | agent_calls=7 tokens=2982 |
| tagged-2 | tagged | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 2 | agent_calls=4 tokens=1509 |
| tagged-2 | tagged | 2 | 1 | deal | 39 | 1 | 0 | 7 | 0 | 5 | agent_calls=7 tokens=3155 |
| tagged-2 | tagged | 3 | 1 | deal | 25 | 0 | 1 | 8 | 0 | 1 | agent_calls=8 tokens=1736 unpriced_accepts=1 |
| tagged-2 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 4 | agent_calls=8 tokens=2705 |
| tagged-2 | tagged | 5 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 6 | agent_calls=8 tokens=4847 |
| tagged-3 | tagged | 1 | 1 | deal | 130 | 1 | 0 | 4 | 0 | 2 | agent_calls=4 tokens=1254 |
| tagged-3 | tagged | 2 | 1 | deal | 30 | 1 | 0 | 7 | 3 | 2 | agent_calls=7 tokens=5535 |
| tagged-3 | tagged | 3 | 1 | deal | 40 | 1 | 0 | 8 | 0 | 4 | agent_calls=8 tokens=2377 |
| tagged-3 | tagged | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 1 | agent_calls=8 tokens=2137 |
| tagged-3 | tagged | 5 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 1 | agent_calls=5 tokens=1352 |
| structured-1 | structured | 1 | 1 | deal | 80 | 0 | 1 | 4 | 0 | 0 | agent_calls=4 tokens=1072 |
| structured-1 | structured | 2 | 1 | deal | 45 | 1 | 0 | 3 | 0 | 0 | agent_calls=3 tokens=999 |
| structured-1 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1907 |
| structured-1 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1653 |
| structured-1 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1634 |
| structured-2 | structured | 1 | 1 | deal | 115 | 0 | 1 | 6 | 0 | 0 | agent_calls=6 tokens=1621 unpriced_accepts=1 |
| structured-2 | structured | 2 | 1 | deal | 45 | 1 | 0 | 5 | 0 | 0 | agent_calls=5 tokens=993 |
| structured-2 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1622 |
| structured-2 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1611 |
| structured-2 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1655 |
| structured-3 | structured | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 0 | agent_calls=6 tokens=1364 |
| structured-3 | structured | 2 | 1 | deal | 42 | 1 | 0 | 5 | 0 | 0 | agent_calls=5 tokens=1154 |
| structured-3 | structured | 3 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=2114 |
| structured-3 | structured | 4 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1492 |
| structured-3 | structured | 5 | 0 | open |  | 0 | 0 | 8 | 0 | 0 | agent_calls=8 tokens=1768 |

## 3. FIPA-ACL against the three conditions

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| **Where the illocutionary force lives** | A `performative` parameter on the message surface, the only one of the 13 that is mandatory. | Nowhere on the surface. Recovered after the fact by a model that reads the sentence. | A parenthesised tag at the head of the message. On the surface. | A `performative` field in a JSON object. On the surface. |
| **Content language** | A declared formal language (`:language`, e.g. `fipa-sl`) over a declared `:ontology`. | English, undeclared. | English, undeclared. | One integer field, `content.price`. The schema is the language and it holds one number. |
| **Who interprets the content** | The receiver, using the shared ontology. The spec says content is "interpreted by the receiver". | A third model call, once per message. | A regex for the act, a model call for the price inside a `propose`. | `json.loads`. No model. |
| **How a conversation ends** | An interaction protocol (`:protocol`) names the terminal states. | An act ends it: 13 of 15 episodes. | An act ends it: 13 of 15. | An act ends it in 6 of 15. The other 9 hit the turn limit. |
| **What guarantees sincerity** | Nothing enforceable. Each act's feasibility precondition is a condition on the sender's beliefs, which does not travel in the message, and the spec puts insincere agents "beyond the current scope". | Nothing enforceable. One sentence in the system prompt, unverifiable from any message. | Same sentence, same lack of enforcement. | Same sentence, same lack of enforcement. A JSON field cannot be sincere. |
| **What a message costs to read** | A parse and an ontology lookup at read time. The ontology agreement itself is paid once, before any message. | One model call per message. 93 calls, 74,088 tokens over 15 episodes. | A regex, plus one call per `propose`. 44 calls, 40,423 tokens. | A parse. 0 calls, 22,659 tokens. |
| **Which failure modes appear** | The precondition cannot be checked from outside; ontologies must be agreed across organisations before anything can be said. | The four-act vocabulary has no label for a question or a counter-offer, so the reader must force one of four. Here the reader's own price on an acceptance disagreed with the recorded deal price in 2 of 9 deals. | The tag is read and the sentence is not, so a counter-offer inside a rejection never reaches the layer: 38 of 39 `reject-proposal` sentences carried a number that was never read. A role-confusion spiral also dropped the tag for 3 consecutive messages. | Every rejection carried a price in the JSON, 44 of 44, parsed perfectly and then discarded because only `propose` updates the price. `refuse` never appeared at all, so an impossible scenario could not terminate. |

## 4. Interpretation

Changing the message format moved the cost of reading and it moved the kind of
failure, and it moved neither of them in the direction the correctness column
would suggest. The cost axis is clean and monotone: 93 reader calls, then 44,
then 0, and tokens from 74k down to 23k, a factor of 3.3. But `correct` runs the
other way, 11, 10, 4, so the cheapest reader bought the worst outcomes. The
reason is one gap in the act vocabulary. A negotiator's normal move is to reject
and counter in the same breath, and there is no act for that, so each format
handles it differently: `free` survives because the reader labels by intent
rather than by declaration and returned `propose` for 72 of 93 messages, many of
which were rejections carrying a new number; `tagged` takes the declared tag at
face value and never looks at the sentence, losing the counter in 38 of 39
rejections; and `structured` is worst precisely because its schema offers a
`price` field on every act, so attaching a number to a rejection is the natural
thing to write, and all 44 of them were parsed correctly and then thrown away by
a layer in which only `propose` sets a price. The explicit performative is
cheaper to read and less faithful to intent, and that is the whole trade. What no
format touched is the violation count, identical at 2 in all three conditions,
and the reason is that not one of those six is an agent agreeing outside its own
limit. All six are the same bookkeeping artifact of the rule that a deal is
priced at the other side's last `propose`: when the final number arrives inside
an acceptance or inside a rejection, the rule reaches back past it to a stale
offer. In 45 episodes there are 23 deals and **0 agent violations**. The sincerity
that FIPA could not enforce was never actually tested here, and everything the
violation column recorded was the protocol layer measuring itself.

That last claim rests on two grades of evidence, which `analyze.py` separates
rather than pools. In four of the six the agents wrote the number down: both
closing acceptances name the same price and it is inside both limits, so the
recorded breach is contradicted by the transcript. In the other two, both in
`structured`, the acceptances carry `"price": null` and name nothing, so the
agreement has to be read off the last number either side put on the table, 120
and 150, each legal for both agents. That reading is the natural one and no
other number was in play, but it is an inference and it is marked as one. What
does not depend on the inference is the direct check: across all 45 episodes,
**not one acceptance says in its own sentence that the price is outside the
sender's limit**, which is exactly the shape the reference run's four real
breaches took.

### Evidence

Quoted log lines are verbatim except that an em-dash in the model's own text
is written `--` here; `logs/` holds the originals.

**Both agents agree at a legal price, the harness records an illegal one.**
Scenario 1, `reserve` 120, `budget` 150. Agreeing at exactly 120 is legal.

```
logs/free-1.txt, scenario 1
[buyer]  Alright, you've got a deal--I'll take the bicycle for 120.
  [reader] {'performative': 'accept-proposal', 'price': 120}
[seller] Perfect! We have a deal--the bicycle is yours for 120.
  [reader] {'performative': 'accept-proposal', 'price': 120}
[result] outcome=deal price=115 correct=0 violation=1 turns=8 format_errors=0 reader_calls=8
```

The reader read 120 twice. The deal was recorded at 115, the buyer's last
`propose`, because the buyer's own 120 arrived inside an acceptance and an
acceptance does not update a price. 115 is below the seller's reserve, so the
episode is scored as a violation the agents did not commit. `tagged-1` produces
the identical pair on the same scenario, and `tagged-2` and `free-2` produce it on
scenario 3, where both agents say 40 and the harness records 25 and 38.

**The counter-offer inside a rejection, parsed and discarded.**

```
logs/structured-1.txt, scenario 5 (reserve 300, budget 150, no deal possible)
[seller] {"performative": "reject-proposal", "content": {"price": 500}}
  [parser] {'performative': 'reject-proposal', 'price': 500}
[buyer]  {"performative": "reject-proposal", "content": {"price": 120}}
  [parser] {'performative': 'reject-proposal', 'price': 120}
[seller] {"performative": "reject-proposal", "content": {"price": 450}}
  [parser] {'performative': 'reject-proposal', 'price': 450}
```

Nothing is misread. Every number is parsed exactly. The two agents converge from
500 and 100 towards each other over eight turns and the protocol layer records
none of it, because the act they declared says only "no".

**The format that could not say no.** `refuse` appears 4 times in `free`, 5 in
`tagged`, and 0 times in 101 `structured` messages. Scenarios 4 and 5 cannot end
in a deal, and in `structured` all six of those episodes ran to the turn limit
and were scored `open`, which is what drops `correct` to 4.

**Agents tag themselves when not asked to.** The `free` format paragraph asks for
plain English and says nothing about tags, yet 28 of 93 `free` messages carry an
act marker the agent added on its own.

```
logs/free-1.txt
[buyer] Alright, you've got a deal--I'll take the bicycle for 120.  **accept-proposal**
logs/free-3.txt
[buyer] You've got a deal at $40--that's fair, and I'm happy with that price.  **Act: accept-proposal**
```

Naming the four acts in the shared paragraph was enough to produce the tag
without asking for it, which weakens the free/tagged contrast on the generation
side even though the reading cost still differs by a factor of two.

**The protocol breaking down and being repaired in-band.** In `tagged-3`,
scenario 2, the seller dropped the tag and the two agents spent three messages
arguing about whose turn it was, which is the whole of that condition's format
error count.

```
logs/tagged-3.txt, scenario 2
[seller] I'm ready to negotiate with you as the buyer! Please make your first
         counter-offer or response to my opening proposal of $30 for the desk lamp.
  [regex] {'performative': None, 'price': None}   # unread, format_errors += 1
[buyer]  I appreciate your readiness, but I want to clarify the roles: according to
         my instructions, **I am the buyer** with a private budget limit of $45.
  [regex] {'performative': None, 'price': None}   # unread, format_errors += 1
```

The buyer states its private budget out loud while repairing the protocol. The
episode recovered on the next turn and ended in a correct deal at 30.

### A stated limit

The deal-price rule is the one the assignment specifies, and it is kept as
specified. A rule that instead priced a deal from the number named in the
acceptance itself would have recorded 120, 40, 120 and 40 in the four plain-text
cases above, all of them legal, and the violation count would be 0 in every
condition. That change is not made here, because changing the measuring
instrument after reading the measurement is how a result stops meaning anything.
It is reported instead: in this run the violation column measures the harness,
not the agents, and a run designed to measure agent sincerity would need a
different rule and a scenario in which an agent has a reason to break its limit.
