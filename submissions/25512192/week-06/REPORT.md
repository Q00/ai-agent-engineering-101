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
