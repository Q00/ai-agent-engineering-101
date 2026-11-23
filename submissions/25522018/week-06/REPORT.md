# Week 06 — A2A v1.0 Negotiation (Student 25522018)

## 1. Harness, model and run commands

The seller and buyer use GroqCloud's `openai/gpt-oss-20b` via HTTPS chat completions. `llm_harness.py` requests JSON decisions non-interactively. The A2A JSON-RPC layer is implemented using `a2a-sdk` 1.2.x. All secrets come from `GROQ_API_KEY` at runtime.

The commands in `agent.json` are `python seller.py --port {port}` and `python buyer.py --url {url} --item {item} --budget {budget}`. Every A2A buyer POST includes the `A2A-Version: 1.0` header, and subsequent messages carry the original `taskId`.

## 2. How agents decide; prompts, data and enforcement

The shop model receives the selected product, reserve, observed offer, prior ask, and recent offer history. It returns `{"action":"ask|counter|accept|reject","price":integer}`. The server enforces hard floors on asks and counteroffers and never completes a sale below the product's reserve. It publishes an Agent Card with a negotiation skill, uses `TASK_STATE_INPUT_REQUIRED` while bargaining, and adds a receipt artifact containing a numeric `price` before completing a deal. The model chooses the negotiation actions; the code checks them for safety.

The buyer model reads the seller's Agent Card and the returned task status, including the last asking price. It returns `{"action":"offer|reject","price":integer}`. The buyer clamps every model-proposed offer to its own budget and validates the receipt before declaring a purchase. Each negotiation preserves a short history of asks and offers, rather than treating the exchange as isolated messages. A2A provides a task lifecycle but does not provide a pricing or acceptance policy.

## 3. Official Trade Test Results

The official Week 6 trade test was executed on Windows using the A2A SDK and GroqCloud (`openai/gpt-oss-20b`).

**Result: 21/21 passed — PASS**

The test validated the seller and buyer negotiation behavior, including successful deals, enforcement of economic limits, A2A communication headers, and task continuation.

The official checker also returned `all checks passed`. The generated `test-report.json` and `logs/trade-test.log` provide reproducibility evidence.

## 5. Challenge 1 — Large Catalog

The challenge used a catalog of 1,763 products and natural-language product retrieval.

The buyer requested wooden storage for approximately 40 books, with a budget of 420. The system retrieved a bookshelf candidate and completed a negotiation through A2A.

**Observed results:**
- Selected product: Bookshelf
- SKU: `BOOKSHELF-0007`
- Purchase price: 260
- Buyer budget: 420
- A2A task state: `TASK_STATE_COMPLETED`
- Negotiation rounds: 2
- Challenge result: PASS

The generated receipt confirmed the selected product and agreed price. However, the receipt alone does not establish whether the requested height constraint was satisfied.

## 6. Challenge 2 — Multiple Shops and Shared Budget

The multi-shop experiment used a directory of 20 available shops and a shared budget of 750.

| Item | Shop | Purchase price |
|---|---|---:|
| Lamp | shop-00 | 110 |
| Bookshelf | shop-02 | 300 |
| Chair | shop-01 | 200 |
| **Total** | | **610** |

All three items were purchased successfully, with distinct task identifiers and no duplicate items.

The total expenditure was 610, leaving 140 of the shared budget unused. Each purchase remained within its allocated spending ceiling, and the final total remained below the global budget.

**Challenge result: PASS**

Evidence is available in `logs/challenge-2/shopping-results.json`.

## 7. Limitations and reproducibility

LLM output can vary, requests may be rate-limited, and occasional retries may change completion-token settings. Hard budget and reserve enforcement is deterministic, but whether the agents reach an agreement is not. The scripted course tests and optional challenges must be run against the real harness and reported without changing any result rows by hand. API keys must never be committed.
