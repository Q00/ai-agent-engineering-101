# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

## 1. Setup

| | |
|---|---|
| provider / endpoint | `api` — `https://api.openai.com/v1` |
| model | `gpt-4o-mini` |
| temperature | `0.0` |
| max tokens | `400` |
| turn limit | 8 messages counting both sides, then the episode ends `open` |
| scenarios | 4, in `scenarios.json`, committed before the first run |
| repeats | 3 per condition per scenario, 36 episodes |

### Scenarios

| id | item | reserve | budget | deal_possible | zone |
|---|---|---|---|---|---|
| 1 | a used aluminium mountain bike | 120 | 300 | 1 | +180 |
| 2 | a second-hand espresso machine | 245 | 265 | 1 | +20 |
| 3 | an electric guitar with one buzzing fret | 400 | 380 | 0 | -20 |
| 4 | an antique oak writing desk | 900 | 250 | 0 | -650 |

`id 1` has a wide zone of agreement and is the baseline: a condition that
cannot close this one has a problem that is not about message format. `id 2`
is narrow, so a single careless concession is a limit breach. `id 3` misses by
the same 20 that `id 2` spans, which separates an agent that holds its limit
from one that simply settles when the numbers are close. `id 4` misses by 650
and should end cleanly.

Four scenarios is the assignment's minimum, chosen because the reference run
spent about 340 model calls on six. A fifth with `reserve == budget` was
written and dropped for the same reason; it is in the commit history.

### The role paragraph, identical in all three conditions

```text
You are the buyer of {item}, negotiating the price with the seller. Your private limit: you can pay at most {limit}. Never agree to a price above {limit}. You speak first, and the two of you then take turns, one message each.
```

```text
You are the seller of {item}, negotiating the price with the buyer. Your private limit: you can accept at least {limit}. Never agree to a price below {limit}. The buyer speaks first, and the two of you then take turns, one message each.
```

```text
 Four acts are available: propose (offer a price), accept-proposal (agree to the other side's last price, which ends the negotiation with a deal), reject-proposal (decline the last price and keep negotiating), refuse (leave the negotiation for good, no deal). The negotiation ends after 8 messages counting both sides; if no deal has been agreed by then, there is no deal.
```

### The format paragraph, the only thing that differs

```text
free:       Write your message as one or two plain English sentences.
```

```text
tagged:     Start your message with exactly one performative tag in parentheses, one of (propose), (accept-proposal), (reject-proposal), (refuse), then write one plain English sentence.
```

```text
structured: Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.
```

### The reader prompt, identical in `free` and `tagged`

```text
You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. Set price to the amount the last message itself puts forward, and to null when the last message names no amount of its own. Every message must be labelled with one of the four acts; if the last message is none of them, choose the one closest to what it does.
```

### How `correct` and `violation` were decided

`correct` is 1 when a deal happened exactly where one was possible, at a price
inside both limits; an episode that ended without a deal is correct when no
zone of agreement existed, whether it refused or ran out of turns. `violation`
is 1 when a deal closed below the reserve or above the budget. They are
separate columns because an episode can be wrong without either agent breaking
its limit — a reader that misreads a price does exactly that.

### How to run

```powershell
python verify_offline.py     # 27 checks of the counting, no API calls
python runner.py --all       # the whole lab, resumable
python report.py             # regenerate this file
python ..\..\..\scripts\check_week04.py .
```

## 2. Results

### Per condition

| condition | correct / n | deal, no_deal, open | violation | mean turns | format errors | reader calls |
|---|---|---|---|---|---|---|
| `free` | 5 / 12 | 1, 4, 7 | 1 | 7.1 | 0 | 85 |
| `tagged` | 12 / 12 | 6, 3, 3 | 0 | 6.8 | 0 | 39 |
| `structured` | 9 / 12 | 3, 0, 9 | 0 | 7.0 | 0 | 0 |

### Per episode

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| free-1 | free | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 8 | tokens=3957 calls=16 |
| free-1 | free | 2 | 1 | open |  | 0 | 0 | 8 | 0 | 8 | tokens=3938 calls=16 |
| free-1 | free | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 8 | tokens=4487 calls=16 |
| free-1 | free | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 | tokens=4125 calls=16 |
| free-2 | free | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 8 | tokens=4005 calls=16 |
| free-2 | free | 2 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 4 | tokens=1668 calls=8 |
| free-2 | free | 3 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 | tokens=3669 calls=14 |
| free-2 | free | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 | tokens=4143 calls=16 |
| free-3 | free | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 8 | tokens=4119 calls=16 |
| free-3 | free | 2 | 1 | no_deal |  | 0 | 0 | 4 | 0 | 4 | tokens=1638 calls=8 |
| free-3 | free | 3 | 0 | deal | 430 | 0 | 1 | 7 | 0 | 7 | tokens=3540 calls=14 |
| free-3 | free | 4 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 7 | tokens=3442 calls=14 |
| tagged-1 | tagged | 1 | 1 | deal | 270 | 1 | 0 | 8 | 0 | 4 | tokens=2888 calls=12 |
| tagged-1 | tagged | 2 | 1 | deal | 245 | 1 | 0 | 8 | 0 | 4 | tokens=2826 calls=12 |
| tagged-1 | tagged | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 4 | tokens=3013 calls=12 |
| tagged-1 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 | tokens=845 calls=4 |
| tagged-2 | tagged | 1 | 1 | deal | 290 | 1 | 0 | 8 | 0 | 4 | tokens=2976 calls=12 |
| tagged-2 | tagged | 2 | 1 | deal | 245 | 1 | 0 | 8 | 0 | 4 | tokens=2862 calls=12 |
| tagged-2 | tagged | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 4 | tokens=3016 calls=12 |
| tagged-2 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 | tokens=845 calls=4 |
| tagged-3 | tagged | 1 | 1 | deal | 300 | 1 | 0 | 8 | 0 | 4 | tokens=2962 calls=12 |
| tagged-3 | tagged | 2 | 1 | deal | 245 | 1 | 0 | 8 | 0 | 4 | tokens=2855 calls=12 |
| tagged-3 | tagged | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 4 | tokens=3008 calls=12 |
| tagged-3 | tagged | 4 | 0 | no_deal |  | 1 | 0 | 3 | 0 | 1 | tokens=845 calls=4 |
| structured-1 | structured | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | tokens=2272 calls=8 |
| structured-1 | structured | 2 | 1 | deal | 255 | 1 | 0 | 4 | 0 | 0 | tokens=969 calls=4 |
| structured-1 | structured | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | tokens=2288 calls=8 |
| structured-1 | structured | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | tokens=2272 calls=8 |
| structured-2 | structured | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | tokens=2272 calls=8 |
| structured-2 | structured | 2 | 1 | deal | 255 | 1 | 0 | 4 | 0 | 0 | tokens=969 calls=4 |
| structured-2 | structured | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | tokens=2288 calls=8 |
| structured-2 | structured | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | tokens=2272 calls=8 |
| structured-3 | structured | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | tokens=2272 calls=8 |
| structured-3 | structured | 2 | 1 | deal | 255 | 1 | 0 | 4 | 0 | 0 | tokens=969 calls=4 |
| structured-3 | structured | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | tokens=2288 calls=8 |
| structured-3 | structured | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | tokens=2272 calls=8 |

## 3. FIPA-ACL against the three conditions

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| where the illocutionary force lives | a mandatory `performative` parameter in the message envelope, one of the 22 acts in SC00037J | nowhere in the message; it has to be recovered from the conversation | a parenthesised tag at the head of the message, one of four | the `performative` field of a JSON object, one of four |
| what the content language is | declared per message by `:language` with an `:ontology`; SL or KIF in practice | English prose | English prose after the tag | JSON, one field: `content.price`, a whole number or null |
| who interprets the content | the receiving agent, against the FP and RE written for each act | an LLM reader, given the whole transcript, labelling the last message | a regex for the act; the LLM reader only for the price inside a `propose` | `json.loads`, no model involved |
| how a conversation ends | an interaction protocol terminates it (accept-proposal, reject-proposal, failure) | identical in all three by construction: an `accept-proposal` with a price on the table, a `refuse`, or the 8-message limit | ← | ← |
| what guarantees sincerity | nothing observable. The FP demands the sender believe what it says, and that belief never travels with the message — Wooldridge's semantic verification problem | nothing. The private limit sits in the prompt, and only the outcome check catches a breach afterwards | ← | ← |
| what a message costs to read | a parse; no inference | one model call per message | a regex, plus one call per `propose` | a parse; no model call |
| which failure modes appear | force is explicit and cheap to read, but the semantics cannot be checked from outside | 0 unreadable, 0 accept with no price, 0 ended on turn one, 1 limit breach(es) | 0 unreadable, 0 accept with no price, 0 ended on turn one, 0 limit breach(es) | 0 unreadable, 0 accept with no price, 0 ended on turn one, 0 limit breach(es) |

The fourth and fifth rows are identical across the three conditions on
purpose: the termination rule and the absence of any sincerity guarantee are
held fixed so that the differences in part 2 can only come from the format and
the reader.

## 4. Interpretation

<!-- your interpretation goes below this line; report.py preserves it -->

TODO — one paragraph. Which condition moved which metric, and why. Quote the lines below.

<!-- end of your interpretation -->

### Evidence from the logs

**Episodes that ended on turn one (0)**

_none_

**Reader labels worth checking by hand**

- `logs/free-3.txt:51` — buyer: I appreciate your willingness to negotiate, but I can only go up to 380. Would you accept that?
  read as `accept-proposal`, but the message names 380; the deal closed at 430, the other side's standing price — scenario 3

**Deals that broke a private limit (1)**

- `free-3` scenario 3: deal at 430, deal_possible=0 — see `logs/free-3.txt`

**Acceptances with no price on the table (0)**

_none_

**Messages the protocol layer could not read (0)**

_none_

**Crashed episodes (0)**

_none_
