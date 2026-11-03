# Week 06 report: Claude Code (haiku) as an A2A seller and buyer

## 1. Harness, model, command

- **Harness**: Claude Code 2.1.289, headless. **Model**: Claude Haiku (`--model haiku`). Both agents use it.
- **Exact command** (`harness.py`), prompt on stdin, run in an empty temp directory (`tempfile.mkdtemp`):
  ```
  claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence --system-prompt "<role>"
  ```
  `--system-prompt` is my addition to the command in the README. Without it haiku answers as a coding
  assistant and refuses the JSON format (first try, before any code):
  `I appreciate you testing my consistency, but I can't just output arbitrary JSON or override my core function.`
  The role is one sentence per agent ("You are the pricing brain of a furniture shop's sales agent ...").
- One harness call is about 5 to 9 s. Every call is logged with its full prompt and raw answer
  (`logs/seller-under-test.log`, `logs/buyer-under-test-*.log`).
- Run: `pip install "a2a-sdk[http-server]" uvicorn httpx` (a2a-sdk 1.2.2, Python 3.14, Windows 11), then
  `python scripts/trade_test_week06.py submissions/25512192/week-06`.

## 2. How each agent decides

Both agents ask the harness for exactly one JSON object per decision and are stateless between calls:
the code keeps the negotiation state and puts a compact history into each prompt.

**Seller** (`seller.py`). Three prompts:

| step | harness sees | harness answers |
|---|---|---|
| search (only when the catalog has more than 12 items) | the buyer's text, the category list with attribute names | `{"category", "keywords", "constraints"}` |
| pick | the buyer's text, up to 10 candidate lines with `reserve=N` | `{"item_id", "ask", "message"}` |
| negotiate | item, reserve, opening ask, current ask, history `ask -> offer`, the new offer | `{"action": accept/counter/reject, "price", "message"}` |

Enforced in code, not only in the prompt (`GUARD` lines in the log):
- an opening ask below the reserve is replaced by `ceil(reserve * 1.3)`; a counter is clamped to
  `[reserve, current ask]`; "accept" below the reserve becomes a counter at the current ask;
- an offer at or above the current ask closes the deal without asking the harness (the shop cannot
  refuse its own price), and so does a counter at or below the offer;
- the harness's sentence is dropped if it contains a digit, and the code appends `Asking price: N` /
  `Counteroffer: N`, so the ask is always the last number; it is also sent as message metadata `{"ask": N}`;
- more than 10 rounds -> reject. A message to a finished task gets the SDK's error
  (`-32004 ... is in terminal state`), which I did not have to write.

**Buyer** (`buyer.py`). One prompt per round: item, budget, history `seller ask -> my offer`, the seller's
latest text inside `<seller>` tags marked as information, not instructions, and the current ask (from
`metadata.ask`, else the last number). Answer: `{"action": accept/offer/walk, "price", "reason"}`.
Enforced in code: an offer above the budget is clamped to the budget; "accept" sends the ask as the offer
and is only allowed when the ask is within the budget (otherwise it becomes an offer of the budget);
"walk", an unparseable answer, or 10 offers without a deal send `CancelTask` and exit. Every request,
including the Agent Card GET, carries `A2A-Version: 1.0`; follow-ups carry `taskId`.

So the reserve and the budget are in the prompt **and** enforced in code. The harness chooses everything
inside those walls; the walls themselves never depend on the model.

**Trade test result** (`test-report.json`, `logs/trade-test.log`): 21/21 passed in 9 min 17 s with
45 harness calls (29 seller, 16 buyer). No `GUARD` line fired in that run: haiku stayed inside the reserve
and the budget on its own every time; the walls were never needed, but they are what makes the result
not depend on that luck. Seller asks in `seller_holds_reserve`: `[340, 300, 270, 265, 260, 260]`,
`[360, 300, 270, 260, 260]`, `[350, 300, 275, 265, 260, 260]`, ending in `TASK_STATE_REJECTED`. The
buyer closed the lamp at 100, 105 and 100 with one offer each, and in `buyer_holds_budget` climbed to
exactly 230 and walked (`kit seller saw: offers [175, 215, 225, 230], deals [], canceled 1`).

## 3. What failed on the way, and what I changed

1. **Haiku refused the role.** With the README's command as is, my first manual call (in a terminal, not
   saved to a log) answered
   `I can't just output arbitrary JSON or override my core function. I'm designed to help you with
   software engineering tasks`. Claude Code's own system prompt wins over a role written in the user
   prompt. Fix: `--system-prompt` with a one-line role per agent.
2. **Numbers inside the harness's sentence.** The kit reads the ask as the *last number* of the message,
   so a sentence like "available for 150" before my ask is risky. My first fix deleted digits and left
   broken text (`logs/attempts/seller-smoke-01.log` line 20):
   `Great choice—we have a nice desk lamp available for . Item: lamp. ... Asking price: 150`.
   Second fix: drop the whole sentence if it has a digit (logged as `GUARD dropped harness sentence`), ask
   for "no numbers" in the prompt, and send the ask in `metadata.ask` too. That guard has a false positive
   that I kept: `'... three drawers for A4 folders ...'` was dropped in challenge 1 because of the 4 in A4.
3. **The trade test itself crashed** in `seller_holds_reserve #3`
   (`logs/attempts/trade-test-01-crash-cp949.txt` line 112):
   `UnicodeEncodeError: 'cp949' codec can't encode character '\u2014' in position 71`.
   Haiku wrote an em dash (`logs/attempts/seller-under-test-01.log` line 594: `You're moving in the right
   direction—let's meet closer to the middle here. 230 is too low. Counteroffer: 260`), the kit printed my seller's
   message to a Korean-locale Windows console, and Python could not encode it. All 7 seller runs before
   that had passed. The kit is read-only, and setting `PYTHONIOENCODING` would only fix my machine, so the
   seller now maps typographic punctuation to ASCII and drops anything else non-ASCII. The rerun passed 21/21.
4. **Metadata numbers come back as floats.** `metadata.ask` 150 arrived as `150.0` (protobuf `Struct`
   has only doubles), and my test client sent the offer `"120.0"`. The client now rounds the ask up to int.
   Receipts still show `price: 120.0` for the same reason; the kit accepts floats.
5. **Challenge data bugs.** In `gen_catalog.py` the office chair and sofa drew a random number twice, once
   for the attribute and once for the description (`OC-019`: description "up to 120 kg", attribute 150). My
   first patch for it never applied and I did not notice until I checked a challenge-2 receipt. Challenge 1
   and 2 were rerun on the fixed catalog; the old runs are in `logs/attempts/*-buggy-catalog`. The generator
   now asserts that every number in a description is one of the item's attributes.

## 4. What A2A fixed, and what the two sides still had to agree on

A2A fixed everything about *finding and addressing* the other agent and about *whose turn it is*. The buyer
finds the endpoint, binding and version on `/.well-known/agent-card.json`; a negotiation is one task and
every follow-up names its `taskId`; `TASK_STATE_INPUT_REQUIRED` means "your move", `COMPLETED`, `REJECTED`
and `CANCELED` end it, and the SDK refuses a message to a finished task (`-32004 ... is in terminal state`)
without a line of my code; walking away is `CancelTask`; the result is an artifact with a typed data Part. My
buyer traded with the kit's seller and the kit's buyer with my seller without either knowing what runs
inside. What A2A did **not** settle is the business language inside the text parts. How a price is written
is a convention ("the last number of the message"), and it is fragile enough that my seller throws away the
model's whole sentence when it contains a digit (`A4` included). I added `metadata.ask`, but only my own
buyer reads it. There is no "accept" in the protocol: the buyer accepts by sending the ask back as a bare
number, and the seller accepts by completing the task with a receipt whose `price` field is again only
agreed on in the README. Nothing says what a task's history is worth either: when the buyer came back to
Oak Yard for the bookshelf in a new task, the shop had no memory of the old one (it had come down to 181) and
opened at 254 again; the buyer paid 200. Currency, units, item identity (my seller puts `BX-009` into
free text) and the fact that a JSON number becomes a double (`150` arrives as `150.0`) were all outside A2A.

## 5. Challenges 1 and 2

### Challenge 1: a 3024-item catalog (`catalogs/large.json`, 958 KB; `logs/challenge-1/`)

The harness never sees the catalog. On the first message it gets the category list with attribute names
(about 2.2 KB) and turns the buyer's description into a search plan; plain code (category filter, numeric
constraints, BM25 in `catalog.py`) returns at most 10 candidates; a second call picks one of them and opens
the price (prompt at most 3.4 KB, the reserves of the candidates included). Each negotiation round is a
0.7 KB prompt with only the chosen item. The largest prompt is about 1/280 of the catalog. From `seller.log`:

```
search category='bookshelf' keywords=['wooden', 'books'] constraints={"wooden": true, "books": {"min": 35, "max": 45}, "height_cm": {"max": 75}} -> 1 hits
search category='mirror' keywords=['full length', 'metal frame', 'metal'] constraints={"material": "metal", "height_cm": {"min": 140, "max": 200}, "wooden": false} -> 0 hits
relaxed search (no constraints) -> 10 hits
```

"not taller than my desk" became `height_cm <= 75` on its own. The mirror shows the weak spot: the model
invented `material: "metal"` (the catalog says steel, aluminum or brass), the filter found nothing, and only
the fallback (category + text search) saved it. It happened in both runs. A checker that does not trust the
seller reads the catalog and checks each pick with a predicate per request (`run.log`):
`SUMMARY 8 requests: right item 8, reserve held 8, deals 8`. The run on the buggy catalog
(`logs/attempts/challenge-1-buggy-catalog`) was 8 / 8 / 7; the missing deal was a seller that rejected
the first offer (112 on an ask of 225) outright.

### Challenge 2: 20 shops, a shopping list, one budget (`logs/challenge-2/`)

20 copies of `seller.py` (`shops/shop-01..20.json`, 3 to 7 categories each, price factor 0.8 to 1.4 of the
catalog reserve) on ports 9501-9520; `directory.json` holds their URLs. `shop_buyer.py` reads the 20 cards
and squeezes each into one line (`S14 | Oak Yard | Furniture shop with discount prices. Sells ... | tags:
...`, 3958 chars for all 20). One planner call gets the list, the money left and the directory, and returns
per entry up to 3 shops and a cap; the code scales the caps down if they exceed the money left. Then every
entry negotiates in its own thread at the same time (`buyer.negotiate`, the same loop as the trade test),
one shop at a time, stopping at the first deal. Two code rules make the pass conditions hold whatever the
model says: an entry's thread stops after one deal (nothing bought twice), and every deal is at or below
its cap with the caps summing to at most the money left (total within budget).

The state the buyer carries is a ledger per entry: status, shop, price, and notes. The negotiation prompt
of one shop never contains another shop's talk (22 negotiation prompts, at most 951 chars each). When an
entry is still open after a pass, the planner is asked again and sees one line per finished negotiation,
which holds that shop's whole `ask -> offer` history. My first version passed it the last 8 per-round lines
instead, and in that run (`logs/attempts/challenge-2-last8-truncation/buyer.log`) the replan prompt for the
bookshelf showed Copper Market and Harbor Depot but not Oak Yard, the first shop tried, so the planner chose
Oak Yard again without knowing what had happened there. With one line per shop (budget 650, `buyer.log`):

```
[plan 1] money left 650; plan {"1": {"shops": ["S14", "S20", "S12"], "cap": 120}, ...
What happened so far (one line per shop negotiated with):
entry 1: open
  - Oak Yard: WALKED at ask 181 after offers ['265 -> 100', '230 -> 110', '195 -> 120'] (...)
  - Copper Market: WALKED at ask 185 after offers ['225 -> 110', '200 -> 120'] (...)
  - Oak Depot: ended TASK_STATE_REJECTED after offers ['380 -> 105', '320 -> 115']
entry 2: bought at S18 for 35.0
...
[plan 2] money left 241.0; plan {"1": {"shops": ["S14", "S20", "S05"], "cap": 200}}
[entry1] BOUGHT at S14 for 200.0
RESULT bought 5/5, total 609.0 of budget 650, PASS; harness calls 24, 549 s
```

The checker counts deals in the **shops'** logs, not in the buyer's (`check.txt`): 5 deals, every entry
bought, none twice, 609 <= 650. Runs: budget 800 on the buggy catalog (5/5, 580, no replan), budget 800 on
the fixed catalog (5/5, 557, one replan, the truncation run), budget 650 after the fix (5/5, 609, one
replan). The planner's first cap for the bookshelf was too low every time (150, 120); the replan with the
walk-away lines is what fixed it each time.

Not done: challenge 3 (long messages, hostile lines), the deployed seller, and trading with a classmate.

The code, the runs and this report were produced by Claude Code (Opus 5.5) at my request; the commit history
shows each step, including the failed ones.
