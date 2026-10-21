# Week 06 Report

## 1. Harness, model, and command

I used Codex CLI 0.162.1 with the default model configured by Codex.

The harness is called from Python with:

`codex exec --skip-git-repo-check --ephemeral --color never -`

The prompt is provided through stdin. Each harness call runs in a temporary empty directory so that Codex does not read project instruction files.

## 2. Seller and buyer decisions

### Seller

The seller exposes an A2A v1.0 JSON-RPC server and sells lamp, chair, and bookshelf with reserve prices 100, 180, and 260.

For every negotiation decision, the seller sends Codex the item, reserve price, current asking price, buyer offer, and number of previous low offers.

Codex returns one JSON object with one of these actions:

- `ask` with a price
- `counter` with a price
- `accept` with a price
- `reject`

The reserve is stated in the prompt, but it is also enforced in Python code. Asking and counteroffer prices are wrapped with `max(reserve, price)`, and an offer can only be accepted when the buyer offer is at least the reserve. Therefore a model response cannot make the seller ask or sell below its reserve.

### Buyer

The buyer first fetches the seller's Agent Card and then communicates using A2A v1.0. Every POST request includes the `A2A-Version: 1.0` header. Follow-up negotiation messages reuse the task's `taskId` and `contextId`.

For each decision, Codex receives the item, maximum budget, seller's current price, and negotiation round.

Codex returns one JSON object:

- `offer` with a price
- `walk`

The maximum budget is included in the prompt and is also enforced in Python with `min(budget, price)`. This prevents the buyer from paying more than its budget even if the harness returns a larger number.

## 3. What failed and what changed

The first complete run of `trade_test_week06.py` passed all tests, so the generated `logs/trade-test.log` contains no failing test lines to quote. The final result was:

`21/21 passed -> PASS`

Before running the complete trade test, I found problems while adapting the lab skeleton to the installed `a2a-sdk 1.2.2`. The original lecture version did not match the installed API. In particular, the server initially failed with:

`TypeError: DefaultRequestHandlerV2.__init__() missing 1 required positional argument: 'agent_card'`

and then:

`TypeError: create_jsonrpc_routes() missing 1 required positional argument: 'rpc_url'`

I fixed these by passing `agent_card=card` to `DefaultRequestHandler` and using `create_jsonrpc_routes(handler, "/")`.

I then manually tested both agents before running the official trade test. The seller completed a lamp deal at 110 while staying above the reserve, and refused to sell a bookshelf below its reserve. The buyer successfully bought a lamp with budget 120 and produced no deal for a bookshelf with budget 230.

## 4. What A2A settled and what it did not

A2A settled the communication structure between the two agents: where to discover the Agent Card, how to send JSON-RPC messages, how task states such as `input-required`, `completed`, and `rejected` are represented, and how the buyer continues the same negotiation using the task ID. It also gave the seller a standard way to return the final receipt as an artifact. However, A2A did not define the business meaning of the negotiation itself. The buyer and seller still had to agree outside the protocol on how a price is written in a message, how to interpret the last number in an `input-required` response, what counts as accepting an offer, and when either side should stop negotiating. Those decisions were handled by the harness prompts and by the safety checks in the Python code.
