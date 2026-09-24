# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

## 1. Setup

**Provider / model**: Anthropic, `claude-haiku-4-5-20251001` (env var `AGENT_MODEL`, see [model.py](model.py)).

**Temperature**: not settable. The installed `anthropic` Python SDK (1.5.0) no longer exposes a
`temperature` parameter on `Messages.create` — it has been dropped from the Messages API on this
SDK/model. This is recorded rather than invented, the same convention the week-03 note describes
for a CLI that does not expose temperature.

**Turn limit**: `MAX_TURNS = 5` messages (buyer and seller alternate; unresolved after 5 messages
→ `open`). An earlier pass used `MAX_TURNS = 8`; that run is kept, unmodified, under
[`_old_maxturns8/`](_old_maxturns8/) as the discarded attempt — it produced far more `deal`/`no_deal`
outcomes and fewer `open` ones (see part 4).

**Roles**. Every condition shares the same base role prompts (buyer knows only its budget, seller
only its reserve, both restricted to the four FIPA acts `propose` / `accept-proposal` /
`reject-proposal` / `refuse`); only the format paragraph appended to the system prompt changes
([protocol.py](protocol.py)):

- **free** — *"Message format: write your turn as plain, natural English negotiation dialogue, one
  or two sentences. Do not use any tags, labels, or JSON. State a specific numeric price whenever
  you propose a price or accept one."*
- **tagged** — *"Message format: start your reply with exactly one performative tag in parentheses
  -- (propose), (accept-proposal), (reject-proposal), or (refuse) -- followed by one plain English
  sentence. Example: "(propose) I can offer 95 for it." State a specific numeric price whenever you
  propose or accept one."*
- **structured** — *"Message format: reply with exactly one JSON object and nothing else, of the
  form {"performative": ..., "content": {"price": <integer>|null}}. Use content.price for propose
  ... and for accept-proposal ... Use content.price = null for reject-proposal and refuse. Do not
  write any text outside the JSON object."*

**Reader prompt** (used in `free` for every message, in `tagged` only for the price inside a
`propose`; never used in `structured`):

> *"You read one message from a price negotiation between a buyer and a seller. Classify it as
> exactly one of these four communicative acts: propose ...; accept-proposal ...; reject-proposal
> ...; refuse .... Reply with exactly one JSON object and nothing else:
> {"performative": ..., "price": <integer>|null}. Use price only for propose and accept-proposal
> ...; use null otherwise."*

The `tagged` condition uses a second, narrower reader only for the price inside a `propose`
(*"Extract the single numeric price being proposed or accepted in this negotiation message. Reply
with exactly one JSON object ...: {"price": <integer>|null}"*) — an earlier version of the code
also called this reader for `accept-proposal` messages, which the spec does not ask for; it was
removed (see part 4, and the discarded `tagged-r*` logs under `_old_maxturns8/` still show the
extra call).

**Price resolution**. `accept-proposal` never re-reads a price from its own message; the deal price
is always the protocol layer's own state — the price of the last message any reader/parser
classified as `propose` ([negotiation.py](negotiation.py)). This mirrors FIPA's definition of
accept-proposal ("agree to the other side's last proposed price") as a *state-based* resolution
rather than trusting whatever number an agent's own sentence claims to be accepting. Part 4 shows
why this choice matters.

**How to run**:

```bash
export ANTHROPIC_API_KEY=<your key>       # or a .env file next to model.py
python run.py                             # writes results.csv, logs/
python ../../../scripts/check_week04.py . # from this directory
```

`run.py` is safe to interrupt and re-run: it skips `(run, scenario)` pairs already present in
`results.csv`.

## 2. Results

| condition | n | deal | no_deal | open | correct | violations | mean turns | format_errors | reader_calls |
|---|---|---|---|---|---|---|---|---|---|
| free | 12 | 2 | 1 | 9 | 7/12 | 1 | 4.67 | 1 | 56 |
| tagged | 12 | 2 | 0 | 10 | 7/12 | 1 | 4.92 | 0 | 20 |
| structured | 12 | 0 | 0 | 12 | 6/12 | 0 | 5.00 | 0 | 0 |

Per-episode table (from `results.csv`):

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-r1 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 110 | 0 | open |  | 1 | 0 | 5 | 1 | 5 |  |
| free-r1 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r1 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 100 | 1 | deal | 120 | 0 | 1 | 5 | 0 | 5 |  |
| free-r2 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r2 | free | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 90 | 1 | deal | 72 | 1 | 0 | 5 | 0 | 5 |  |
| free-r3 | free | 105 | 0 | no_deal |  | 1 | 0 | 1 | 0 | 1 |  |
| tagged-r1 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r1 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r1 | tagged | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r1 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 100 | 1 | deal | 85 | 0 | 1 | 5 | 0 | 2 |  |
| tagged-r2 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r2 | tagged | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 3 |  |
| tagged-r2 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| tagged-r3 | tagged | 90 | 1 | deal | 75 | 1 | 0 | 4 | 0 | 2 |  |
| tagged-r3 | tagged | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 1 |  |
| structured-r1 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r1 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r2 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 100 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 110 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 90 | 1 | open |  | 0 | 0 | 5 | 0 | 0 |  |
| structured-r3 | structured | 105 | 0 | open |  | 1 | 0 | 5 | 0 | 0 |  |

(scenario 100 = apple, reserve 100/budget 110, deal possible; 110 = bicycle, reserve 150/budget
110, deal impossible; 90 = pineapple, reserve 70/budget 90, deal possible; 105 = laptop, reserve
120/budget 105, deal impossible.)

## 3. FIPA-ACL vs the three conditions

| | FIPA-ACL (2002) | free | tagged | structured |
|---|---|---|---|---|
| Where the illocutionary force lives | A mandatory `performative` field, separate from content | Nowhere explicit — inferred from the sentence | A parenthetical tag the speaker writes, separate from the sentence | A `performative` JSON field, separate from `content` |
| Content language | A declared, formal ontology (e.g. SL) | Free English prose | Free English prose | A fixed two-field JSON schema (`price`) |
| Who interprets the content | The receiving agent, assumed to share the ontology | An LLM reader, every message | Regex for the tag; an LLM reader only for the price inside a `propose` | A JSON parser, no model call |
| How a conversation ends | Protocol-defined (e.g. an accept/reject act in the interaction protocol) | `accept-proposal`, `refuse`, or the turn limit (`open`) | same | same |
| What guarantees sincerity | Nothing formal — FP/RE define felicity conditions, not enforcement | Nothing — the reader's label is trusted as-is | Nothing — the tag is trusted as-is even when it contradicts the sentence | Nothing — the JSON field is trusted as-is |
| Cost to read one message | Assumed free (shared ontology, no interpretation step) | 1 reader call per message (12/12 episodes, every message) | 1 reader call only for a `propose`'s price; 0 for the other three acts | 0 — parsing only |
| Failure modes observed | Documented in the literature: ontology mismatch, non-cooperative agents | Reader misreads a query as `propose` with no price (turn 1, most episodes); 1 outright unparseable reply (format_error); 1 violation from a stale price (below) | Speaker tags a counter-offer as `reject-proposal` instead of `propose`, hiding the new price from the protocol's state (1 violation, below); 0 format errors — the tag itself is trivial to parse | 0 format errors, 0 violations, but 0 deals in 12 episodes — the seller almost never states a counter-price once it has said `reject-proposal`, so nothing narrows the gap and every episode times out |

## 4. Interpretation

The two conditions that ever produced a deal — `free` and `tagged` — also produced the two
violations, and both trace back to the **same underlying failure**, just committed by a different
party. In `free-r2` (scenario 100, apple), the seller's turn 2 opens at 120; the buyer's turn 3
counters at 90 — but the **reader** misclassifies that counter as `reject-proposal` with no price
instead of `propose` with price 90, so the protocol's tracked "last price" stays at 120. The seller
then concedes to 110 in turn 4 (also read as `reject-proposal`, not `propose`), and the buyer says
"I can accept that price of 110" in turn 5 — read correctly as `accept-proposal`. But because
`accept-proposal` resolves to the protocol's *tracked* last-propose price rather than whatever the
reader parsed from the acceptance sentence itself (see part 1), the deal closes at the stale 120 —
above the buyer's own budget of 110, a violation of the *buyer's* limit. In `tagged-r2` (same
scenario), the mechanism is identical but the mislabeling comes from the **agent itself**: the
seller's turn 4 states a new floor ("my minimum ... 100 ... firm") but tags it `(reject-proposal)`,
not `(propose)`. Since tagged's protocol layer only updates its tracked price on a `(propose)` tag,
turn 4's 100 never registers. The buyer's turn 5, "(accept-proposal) I can work with 100," resolves
to the last *tagged* propose — turn 3's 85 — a violation of the *seller's* reserve of 100. The tag
in `tagged-r2` was not a parsing failure (`format_errors` for tagged is 0 across all 12 episodes:
the regex matches every time) — it was a **sincerity failure**: the tag the seller chose to write
did not match the illocutionary force of what it said, and nothing in the protocol can catch that,
which is exactly the FIPA problem the lecture raises: a performative is a self-report, not a
verified fact. `free` pays for this differently — its reader call count is enormous (56 across 12
episodes vs. tagged's 20 and structured's 0) precisely because every message needs one, and yet
that extra cost did not prevent the same class of error; if anything the reader introduced an
*additional* point of failure (its own misclassification) on top of the one tagged already has
(the speaker's). `structured` avoided both failure modes entirely — 0 format errors, 0 violations —
but at the cost of the metric the assignment cares about most: 0 deals in 12 episodes. Its
`content.price = null` rule for `reject-proposal` is airtight (a JSON parser cannot mistake a
`null` for a number the way a free-text reader can mistake a sentence for a rejection), but it also
means a seller that has just said `reject-proposal` has stated *nothing* about where it would go
instead — see `structured-r1`, scenario 100: buyer proposes 80, seller rejects with `price: null`,
buyer proposes 90, seller rejects with `price: null` again, and so on for all 5 turns, the seller
never once emitting a `propose` of its own. Free and tagged sellers routinely put a number in the
sentence even while formally rejecting ("I can't go below 100... reconsider"), which is exactly
what let the free/tagged buyers narrow in on a price at all (correctly or not) — structured sellers,
confined to the schema, apparently treat `reject-proposal` as license to say nothing numeric, so
the gap never closes before the turn limit. The turn limit itself materially shapes all of this:
`_old_maxturns8/results.csv` (8 turns instead of 5, otherwise identical code and scenarios) shows
far more `deal`/`no_deal` outcomes and fewer `open` ones per condition — 5 turns is short enough
that `open` dominates every condition in the current run (9, 10, and 12 of 12 episodes respectively),
which is itself a finding about how little runway these agents need to reach a *wrong* deal (2–5
turns to a violation) versus how much they need to reach a *correct* one.
