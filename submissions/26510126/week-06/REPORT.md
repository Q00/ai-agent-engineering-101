# Week 06 — Claude Code as a seller and a buyer over A2A

Student 26510126.

Both agents are thin A2A shells around one harness call per decision. The seller is
the lab's `seller_agent.py` with its rules swapped for the harness; the buyer is a
JSON-RPC client written with httpx. The trade test passed 21/21 on the first full
run (4 min 21 s, 58 harness calls), and none of the code guards fired.

## 1. Harness, model, command

| | |
|---|---|
| Harness | Claude Code, model `haiku` |
| Command | `claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence`, prompt on stdin (`harness.py`) |
| Working directory | a fresh empty temp directory per process, so no project `CLAUDE.md` or settings are read |
| Calls | one call per decision; 90 s timeout, one retry on a timeout, non-zero exit or unparseable answer |
| Seller | `python seller.py --port {port}` (a2a-sdk 1.2.2, JSON-RPC binding, Agent Card at `/.well-known/agent-card.json`) |
| Buyer | `python buyer.py --url {url} --item {item} --budget {budget}` (httpx, `A2A-Version: 1.0` on every request) |

In the passing run every call succeeded on the first try, 3.4 s to 6.1 s each
(median 4.2 s).

## 2. How each agent decides

**Seller.** On the first message the code finds the item in the catalog (an
unknown item is rejected without a harness call) and asks the harness for an
opening price: `{"price": <int>, "note": "<one sentence, no digits>"}`. On each
offer it sends the item, the reserve, the history of asks and offers, the new
offer and the current ask, and expects
`{"action": "accept" | "counter" | "reject", "price": <int>, "note": "..."}`.
The harness writes the note; the code writes the number and puts it last
(`... Counteroffer: 115.`), because the buyer reads the ask as the last number in
the reply. Digits in the note are stripped for the same reason.

**Buyer.** It reads the card, takes the first JSON-RPC interface, opens a task with
`I want to buy a <item>`, and on every `input-required` reply reads the ask as the
last number and asks the harness for `{"action": "offer" | "walk", "price": <int>}`.
The prompt gives the item, the budget, the history, the ask, and which offer this
is, and warns that a shop may end the talk after a few low offers. Accepting is
offering the seller's ask. Walking away sends `CancelTask`.

**Where the limits live.** In both the prompt and the code. The reserve and the
budget are in the prompt, and code clamps every answer:

| Agent | Code rule |
|---|---|
| seller | an ask below the reserve is raised to the reserve |
| seller | an accept below the reserve becomes a counter at the last ask |
| seller | a counter above the last ask is held at the last ask; a counter at or below the offer becomes an accept |
| seller | an offer at or above the current ask is accepted without a harness call |
| buyer | an offer above the budget is lowered to the budget; above the ask, to the ask; below the last offer, to the last offer |
| buyer | when the ask comes down to the last offer or below, it is offered without a harness call |
| both | no usable answer: the seller holds its last ask, the buyer walks away |

Each rule logs a `GUARD:` line when it changes an answer. In the passing run there
are none: haiku never asked below a reserve or offered above a budget. The two
"without a harness call" rules are decisions the code makes rather than clamps on
the harness. The seller's fired twice (`OUT: offer 120 meets ask 120, accepted by
code` in `logs/seller-under-test.log`); the buyer's is logged as `GUARD:` and never
fired, because the kit seller never came down to the buyer's last offer.

## 3. What failed on the way

**The counter prompt crashed the seller.** The first hand test opened fine and
then died on the first offer:

```
[20:08:26] IN: task a365433b '60'
  File ".../week-06/seller.py", line 124, in execute
    answer = await harness.ask(COUNTER_PROMPT.format(
KeyError: '"action"'
Error: failed to send message: Task a365433b-... is in terminal state: TASK_STATE_FAILED
```

The JSON example appended to the prompt went through `str.format`, which read
`{"action": ...}` as a placeholder. The opening prompt had doubled braces and the
counter prompt did not. The exception surfaced through a2a-sdk as a failed task,
so the buyer saw `TASK_STATE_FAILED` and every later message on that task was
refused. Fix: doubled braces (commit `0d50759`). The log from this run was then
overwritten by the next hand test, which wrote to the same minute-stamped file
name; the original is in commit `d6c75dd`.

**Stripping digits broke a sentence.** The seller's note must not carry numbers,
but haiku wrote them anyway:

```
HARNESS: answer '{"action": "counter", "price": 130, "note": "... so 130 is the best I can offer right now."}'
buyer saw:       "... so is the best I can offer right now. Counteroffer: 130."
```

The ask still came out right, since the code puts the price last, so the test
does not notice. The prompt says "no digits" and haiku ignored it in 1 of the 48
notes across all my logs. Left as is.

**Not a failure, but a near one: the buyer's opening offer.** Against the kit seller
the buyer offered `[90, 100]` in all three `buyer_deal` runs and closed at 100, the
reserve. The kit seller rejects after five offers below the reserve, and the buyer
does not know the reserve. Without the "a few low offers" sentence nothing in the
prompt stops a slow climb like 60, 70, 80, 90, 95, which would end in a rejection.
The sentence was in the prompt from the first run, so I have no failing log for it;
the risk is visible only in the kit seller's code.

## 4. What A2A settled, and what it did not

A2A gave the two programs everything about the exchange itself: where to find the
other side (the card at a well-known URL), how to call it (JSON-RPC, `SendMessage`
with a `taskId`), whose turn it is (`input-required`), how a negotiation ends
(`completed`, `rejected`, `canceled`), and that a finished task refuses more
messages. None of that was written by me, and the kit's agents and mine
interoperated on it without any further agreement. What A2A did not settle is what
the messages mean. A price is free text: the test and my buyer both read "the last
number in the reply", which is a convention, not part of the protocol, and it is
the reason my seller's note had its digits stripped. "Accept" has no message of its
own; it is offering the other side's number back, so a buyer that offered 121 to
an ask of 120 would have bought at 121. "Final offer" cannot be said either: my
seller repeated `Counteroffer: 260.` up to five times and the buyer could only
infer from the repetition that it would not move. The skill in the card carries a
sentence, not a schema, so the format had to go into the skill description as
prose. Even the receipt, the one structured part, came back with `"price": 120.0`
for an integer I put in, because a data Part goes through a protobuf Struct where
every number is a double. A2A fixed the envelope; the price inside it still had to
be agreed on by both sides out of band.
