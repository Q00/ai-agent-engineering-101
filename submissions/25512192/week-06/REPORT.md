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
