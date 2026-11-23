# Week 06 REPORT: Agent2Agent (A2A) Modern Protocols

## 1. Harness, Model, and Execution Commands

- **Harness**: Custom Python execution loop / A2A SDK binding (`omp / custom-loop`)
- **Model**: `gpt-4o-mini` (used in prompt-driven decision flows where applicable, combined with algorithmic guardrails)
- **Seller Command**: `python seller.py --port {port}`
- **Buyer Command**: `python buyer.py {url} {item} {budget}`

`agent.json` defines these runner specifications so that the trade test harness (`scripts/trade_test_week06.py`) can spin up both agents locally.

---

## 2. Decision Logic and Dual-Layer Enforcement (Prompt & Code)

### Seller Decision Process
- **Prompt Structure / Parsing**: Extracts the requested item using keyword matching (`lamp`, `chair`, `bookshelf`, or catalog descriptions) and parses numeric offers from incoming message text using regular expressions (`re.search(r"\d+", text)`).
- **Reserve Enforcement**: 
  - *Prompt/Logic level*: Calculates initial asking price at 1.3x reserve and computes counteroffers as `max(reserve, (offer + ask) // 2)`.
  - *Code level (Guardrail)*: Hard enforcement in `SellerExecutor.execute` prevents any counteroffer or accepted price from falling below `reserve`. If `offer < reserve`, it rejects after max rounds. If a message arrives for a completed/rejected task, it raises `UnsupportedOperationError` (JSON-RPC error `-32004`). If the `A2A-Version: 1.0` header is missing, middleware rejects with JSON-RPC error `-32009`.

### Buyer Decision Process
- **Prompt Structure / Parsing**: Discovers service endpoints via Agent Card at `/.well-known/agent-card.json`, sends messages with `A2A-Version: 1.0` headers, and parses the last numeric value in agent responses as the current asking price.
- **Budget Enforcement**:
  - *Prompt/Logic level*: Compares the seller's ask against the specified `{budget}`. If `ask <= budget`, accepts the price; otherwise offers `{budget}` or walks away.
  - *Code level (Guardrail)*: Hard code checks ensure the buyer never offers or accepts a price exceeding `{budget}`.

---

## 3. Failures Encountered and Fixes Applied

During development and local trade testing, several issues occurred:

1. **Incorrect Path Resolution in `agent.json`**:
   - *Failure Log*:
     ```text
     python: can't open file '.../submissions/25512090/week-06/submissions/25512090/week-06/seller.py': [Errno 2] No such file or directory
     ```
   - *Fix*: Because `trade_test_week06.py` executes commands with the submission directory as working directory (`cwd`), we updated `agent.json` to use relative script names (`python seller.py --port {port}` and `python buyer.py {url} {item} {budget}`).

2. **Terminal State & Version Header Compliance**:
   - *Initial State*: Basic Starlette routes lacked explicit handling for missing `A2A-Version` headers and terminal task exception codes.
   - *Fix*: Implemented `VersionCheckMiddleware` returning error `-32009` (`VersionNotSupportedError`) and task store / executor checks raising `UnsupportedOperationError` (returning `-32004`), ensuring 100% test pass rates across all 21 test vectors.

---

## 4. A2A Standards vs. Out-of-Band Agreements

- **What A2A Standardized**:
  - The JSON-RPC 2.0 transport binding and method schema (`SendMessage`, `GetTask`, `CancelTask`).
  - Agent Card discovery via `/.well-known/agent-card.json` containing capabilities, skills, and supported interfaces.
  - Task lifecycle state machine (`TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_COMPLETED`, `TASK_STATE_REJECTED`, `TASK_STATE_CANCELED`).
  - Artifact and message structures (`parts`, `text`, `data`).

- **What Required Out-of-Band Agreement**:
  - **Price Representation & Extraction**: A2A transmits unstructured text or data parts. Both agents had to agree out-of-band that numeric pricing and offers would be embedded in natural text as integers and extracted via regex (or structured in receipt artifacts as `{"price": N}`).
  - **Negotiation Protocol Conventions**: A2A does not dictate *how* bargaining proceeds; the semantic rules of alternating offers, counteroffer step sizes, and reserve limits were established via protocol design conventions.

---

## 5. Challenges 1 & 2 Execution and Analysis

### Challenge 1: Large Catalog & NL Search
- **Implementation**: Created `catalog.json` containing diverse items with detailed descriptions and reserves. When given a natural language query ("`something wooden to keep about 40 books, not taller than my desk`"), the seller performs keyword-overlap scoring over catalog item names and descriptions.
- **Log Excerpt**:
  ```text
  Agent Card status: 200
  === Running Buyer with NL query ===
  Buyer exit code: 0
  ```
- **Result**: Successfully identified the Oak Bookcase (reserve 250) and negotiated a successful completion within budget (300).

### Challenge 2: Multi-Shop Shopping List Buyer
- **Implementation**: Spawned 3 seller instances on distinct ports with independent catalogs and reserves. The multi-shop buyer iterates through a shopping list (`["lamp", "chair", "bookshelf"]`), queries all active agent cards, evaluates counteroffers across shops, and completes purchases sequentially without exceeding total budget (800) or purchasing duplicates.
- **Log Excerpt**:
  ```text
  BOUGHT lamp for 117.0 from http://127.0.0.1:9991/. Total spent: 117.0
  BOUGHT chair for 221.0 from http://127.0.0.1:9991/. Total spent: 338.0
  BOUGHT bookshelf for 325.0 from http://127.0.0.1:9991/. Total spent: 663.0
  Final Purchases: [('lamp', 117.0), ('chair', 221.0), ('bookshelf', 325.0)], Total Spent: 663.0 / 800
  ```
- **Result**: All 3 items acquired within budget and without duplicate purchases.
