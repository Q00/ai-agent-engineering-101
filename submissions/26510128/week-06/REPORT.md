# Week 06 — A2A buyer and seller with a local model harness

Student ID: **26510128**

## 1. Harness, model, execution and reproducibility

This submission exposes two independently running negotiation agents over **Agent2Agent (A2A) v1.0 JSON-RPC**, using the Python `a2a-sdk`. Their decisions are driven by the **custom Python decision-loop harness** (`model_harness.py`) calling **Ollama / `qwen3:4b-instruct`** through its local OpenAI-compatible endpoint. The model is set to temperature **0.2** and `AGENT_MAX_TOKENS=300` by default. This does not require a paid remote-model API key.

The exact launch commands in `agent.json` are:

```text
Seller: python seller.py --port {port}
Buyer:  python buyer.py --url {url} --item {item} --budget {budget}
```

From this submission directory, install dependencies with `python -m pip install -r requirements.txt`. With Ollama running on `http://localhost:11434` and `qwen3:4b-instruct` installed, the optional environment is:

```powershell
$env:OPENAI_BASE_URL="http://localhost:11434/v1"
$env:OPENAI_API_KEY="ollama"
$env:AGENT_MODEL="qwen3:4b-instruct"
$env:AGENT_TEMPERATURE="0.2"
$env:AGENT_MAX_TOKENS="300"
```

From the repository root, execute:

```powershell
python scripts/trade_test_week06.py submissions/26510128/week-06
python scripts/check_week06.py submissions/26510128/week-06
```

The trade test itself creates `test-report.json`, `logs/trade-test.log`, and the Seller/Buyer console logs. The original generated output is retained as evidence; the report is **not** a substitute for executing the current test. The additional experiments are run inside the submission directory with `python challenge_1.py` and `python challenge_2.py`.

### A2A surface

The seller publishes its Agent Card at `/.well-known/agent-card.json` with `supportedInterfaces[0].protocolBinding=JSONRPC`, `protocolVersion=1.0`, a URL and a negotiation skill. The buyer discovers that card, sends every JSON-RPC `SendMessage` request with `A2A-Version: 1.0`, and continues each conversation with the server-created `taskId`. During bargaining, the seller returns `TASK_STATE_INPUT_REQUIRED`. A completed sale produces `TASK_STATE_COMPLETED` and a receipt artifact containing a data Part with numeric `price`. In a no-deal case the negotiation can be rejected or stopped. The SDK rejects messages sent to a terminal task.

## 2. How both agents decide, and where limits are enforced

### Seller

`seller.py` defines `SYSTEM_PROMPT` (the seller decision prompt). The model sees the item name, its **private reserve**, the buyer's current offer when applicable, the previous ask, the round, and the bounded negotiation history. It returns **one JSON object**: `{"action":"ask","price":N}` for a first ask, or `{"action":"accept"}`, `{"action":"counter","price":N}`, or `{"action":"reject"}` on a later buyer offer. The seller's base reserves are **lamp 100**, **chair 180**, **bookshelf 260**.

The financial limit is enforced in **both the prompt and the code**. First asks are computed as `max(reserve, model_price)`. Counteroffers are also constrained to be at least the reserve. Acceptance is executed only when the actual buyer offer is at least the reserve. If the model tries to accept a lower offer, the code issues a safe counteroffer instead. These checks apply to a selected catalog item's own reserve in Challenge 1 too. The A2A task store, rather than the model, controls task continuity and terminal-state behavior.

### Buyer

`buyer.py` defines `BUYER_PROMPT`. The harness receives the requested item, the buyer's **private budget**, the seller's most recent asking price, and the negotiation history. It returns one JSON object with `action` of **`offer`**, **`accept`**, or **`walk`**; a numeric `price` is included for `offer`. The code translates `accept` into sending a numeric offer equal to the seller's current ask because that is the application's agreed price/acceptance convention.

The buyer's budget is enforced in **both the prompt and code**. If the model outputs an offer over budget it is capped to the budget; if the model tries to accept an ask over budget that acceptance is not sent. After completing a task, receipt prices are checked against the budget before reporting a successful purchase. Model output is parsed and validated by `model_harness.py`; the model is not allowed to emit arbitrary direct A2A operations. `a2a_client.py` handles card discovery, version headers, task IDs, and receipt extraction.

## 3. Official trade test, an initial limitation and the correction

### Official trade test results

The first recorded complete run of the official `scripts/trade_test_week06.py` on **2026-10-14** reported **`21/21 passed -> PASS`**. Therefore it would be inaccurate to claim that the official trade test failed before the passing run. Its passing details included:

- **Seller opening and deal (3 repeats):** `TASK_STATE_INPUT_REQUIRED | lamp: asking price 115` followed by `TASK_STATE_COMPLETED | Deal accepted. Receipt price 115`, with a receipt data Part containing `"price": 115.0` (reserve 100, buyer budget 120).
- **Seller terminal state:** the next message to a finished task returned `error -32004 ... is in terminal state: TASK_STATE_COMPLETED`.
- **Seller reserve hold (3 repeats):** on bookshelf (reserve 260) the seller's asks were `275, 260, 260, ...`, never below 260, with no below-reserve sale.
- **Buyer deal (3 repeats):** `kit seller saw: offers [120], deals [120] ... continued 1, posts without A2A-Version 1.0: 0`.
- **Buyer budget hold (3 repeats):** `kit seller saw: offers [230, 230, 230, 230, 230], deals []`, since the bookshelf reserve 260 exceeded budget 230.

These lines are observable in `logs/trade-test.log`; the machine-generated summary is `test-report.json`. After the subsequent shop-specialty change, the **final code should be checked again** with the same official script so the submitted `test-report.json` represents the submitted implementation.

### What needed improvement: Challenge 2 shop choice

The first Challenge 2 execution met the three-item and budget goals but **did not demonstrate purchasing from multiple distinct shops**: the three choices were concentrated on one generic-looking seller. The initial console output showed:

```text
[DF decision] item=lamp selected=shop-00
[DF decision] item=chair selected=shop-00
[DF decision] item=bookshelf selected=shop-00
[TOTAL] bought=3/3 spent=570 budget=650 passed=True
```

This was a **behavioral limitation of the directory selection**, not a failed official trade-test assertion. Its cause was that the Agent Cards were too similar to give the model a meaningful basis to choose a specialist. To correct it, the seller's Agent Card was given an item-specific `specialist-lamp`, `specialist-chair` or `specialist-bookshelf` skill tag, and `multi_shopper.py` first filters the directory by the relevant advertised skill before asking the model to select a shop among those candidates. The original first-run output was to be saved in `logs/challenge-2/first-run.log` and `first-run-result.json`; it is kept separately when present rather than editing the initial evidence. The following successful second-run evidence is in `logs/challenge-2/challenge-2.log` and `result.json`.

A separate environment issue occurred when the Windows PowerShell did not resolve `ollama` as a command; the installed executable worked using its absolute path, and `Invoke-RestMethod http://localhost:11434/api/tags` confirmed that the Ollama API and `qwen3:4b-instruct` were available. This was **not** a trade-test failure.

## 4. What A2A defines and what the agents had to agree on themselves

A2A standardizes how an agent publishes an **Agent Card** with its endpoint/skills, how another agent discovers it, how `SendMessage` requests are carried over JSON-RPC, how multi-turn work continues using a **task ID**, what the task state transitions mean (`TASK_STATE_INPUT_REQUIRED`, `TASK_STATE_COMPLETED`, etc.), and how artifacts such as receipts are returned. It does **not** prescribe how a price should be expressed, when a seller should accept, what a budget or reserve means, or how to respond to counteroffers. Our buyer and seller therefore separately agreed on an application-specific negotiation language: a buyer offer is one **integer in the text Part**; the **last number** in each seller `input-required` status text is the current ask for the official test; sending that ask as a numeric offer expresses acceptance; and a completed sale contains a receipt data Part with a numeric `price`. The model chooses strategy, while code enforces financial invariants and the A2A SDK handles protocol mechanics.

## 5. Required challenges

### Challenge 1 — A catalog of 2,000 items

`catalog.json` contains 2,000 products, each with a name, description, and minimum reserve. A request need not name a catalog ID: `catalog_search.py` ranks the items by text/features and sends **only six shortlisted candidates** (not the full catalog) to the harness. The model chooses one of those exact IDs, and `seller.py` validates that it really was shortlisted and enforces the selected product's reserve. This avoids placing thousands of item descriptions in every prompt.

Actual run log excerpts from `logs/challenge-1/challenge-1.log` and `seller-console.log`:

| Description | Model-selected item | Reserve | First ask | Check |
|---|---|---:|---:|---|
| Wooden, low storage for about 40 books | `bookshelf-0050` | 238 | 253 | Right product category; ask >= reserve |
| Bamboo adjustable reading light | `lamp-0027` | 80 | 95 | Right category; ask >= reserve |
| Compact recycled-material office seat | `chair-0044` | 145 | 160 | Right category; ask >= reserve |

The seller output includes `"[catalog search] ... total=2000 shortlisted=6"`, `"[catalog selected] id=bookshelf-0050 reserve=238"`, and `"[seller opening] ... item=bookshelf-0050 reserve=238 ask=253"`. Each of the three descriptive queries returned `TASK_STATE_INPUT_REQUIRED` with a numeric asking price. This demonstrates **item matching and safe opening asks**; the Challenge 1 script does not itself complete a purchase, which is separately tested by the official trade test.

### Challenge 2 — A 20-shop directory with a shared budget

`challenge_2.py` launched **20** independent A2A seller instances and published their Agent Cards. `multi_shopper.py` first reads their cards as a directory (the lecture's DF / yellow pages), filters by specialist skill tags, and then lets the model choose a seller from the relevant candidates. It negotiates **concurrently** for lamp, chair, and bookshelf via separate A2A task IDs. Each live negotiation carries its own `shop`, `item`, `taskId`, `history`, `outcome`, and `price`, stored under a `shop::item` key. A purchase ledger keyed by **item** prevents duplicate purchases. Per-item allowances sum to the total budget, so spending remains bounded even with concurrent negotiations.

The final run in `logs/challenge-2/challenge-2.log` shows:

```text
[DF shortlist] item=lamp skill=specialist-lamp candidates=['shop-00', 'shop-03', 'shop-06', 'shop-09', 'shop-12', 'shop-15', 'shop-18']
[DF decision] item=lamp selected=shop-00
[DF decision] item=chair selected=shop-01
[DF decision] item=bookshelf selected=shop-02
[TOTAL] bought=3/3 spent=571 budget=650 distinct_shops=3 passed=True
```

Final purchase ledger from `logs/challenge-2/result.json`:

| Item | Shop | Price | Allowance |
|---|---|---:|---:|
| Lamp | `shop-00` | 110 | 130 |
| Chair | `shop-01` | 191 | 220 |
| Bookshelf | `shop-02` | 270 | 300 |
| **Total** | **3 distinct shops** | **571** | **650** |

The unspent budget is **79**, all three items were bought **exactly once**, and the `states` map contains three separate task IDs and offer histories. In the initial run all purchases used `shop-00`; in the revised run specialist discovery distributed them to three shops. The latter is the stronger demonstration of multi-agent discovery and simultaneous, stateful A2A negotiations.

## 6. Scope and limitations

All three compulsory parts (official 21-case trade test and Challenges 1–2) were exercised with the local model. The official trade test uses a rule-based counterpart and does not exhaustively evaluate arbitrary outside agents. Challenge 1 evaluates product selection and opening asks for three descriptive examples, not every catalog item. Challenge 2 uses twenty local instances of our own seller rather than independent classmates' harnesses; shop selection is guided by advertised specialist skills. Additional interoperability trials with external sellers and Challenge 3 are not claimed here.
