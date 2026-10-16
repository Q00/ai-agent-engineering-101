# Week 06 Report - A2A Buyer and Seller

Student ID: 25512089

## 1. Harness, model, and exact commands

I used a custom Python harness backed by GroqCloud.

- Provider: GroqCloud
- Model: openai/gpt-oss-120b
- Base URL: https://api.groq.com/openai/v1
- A2A SDK: a2a-sdk 1.2.2
- A2A protocol: 1.0
- Seller transport: JSON-RPC

The API key is provided through the OPENAI_API_KEY environment variable and is not committed.

Base commands from agent.json:

Seller:
python seller.py --port {port}

Buyer:
python buyer.py {url} {item} {budget}

Official trade test result:

21/21 passed -> PASS

## 2. How the seller and buyer decide

The base seller sells lamp, chair, and bookshelf with reserves 100, 180, and 260.

The harness decides the opening ask, counteroffers, acceptance, and rejection. It returns JSON such as:

{"action":"ask","price":120}
{"action":"accept"}
{"action":"counter","price":110}
{"action":"reject"}

The reserve is included in the prompt and is also enforced in Python. The code never allows an ask or counter below reserve and never accepts an offer below reserve.

The buyer reads the seller Agent Card, selects the JSON-RPC v1.0 endpoint, and sends every request with A2A-Version: 1.0. Follow-up messages continue using the same taskId.

The buyer harness decides every offer and when to walk away. Its response is either an offer or walk action. The item budget is included in the prompt and is also enforced in code, so an offer cannot exceed the authorized budget.

## 3. Failures and fixes

During development, the first seller implementation failed with:

TaskUpdater.update_status() got an unexpected keyword argument 'final'

I replaced the incompatible call with the SDK requires_input(...) helper for TASK_STATE_INPUT_REQUIRED.

Another manual run failed with:

Error: failed to send message: Error code: 400

Because the seller depends on external model output, I hardened the harness with API retries, JSON extraction recovery, and semantic validation retries.

After the fixes, the official base trade test passed:

21/21 passed -> PASS

The final base test also confirmed that the bookshelf seller did not ask or sell below reserve 260 and that the buyer did not purchase above its budget.

## 4. What A2A fixed and what remained outside A2A

A2A standardized discovery, JSON-RPC communication, taskId continuation, task states, and completion artifacts. The buyer could discover the seller through the Agent Card, continue the same task, observe TASK_STATE_INPUT_REQUIRED and TASK_STATE_COMPLETED, and receive a structured receipt.

A2A did not define the application-level meaning of negotiation. The agents still had to agree on item names, how numeric offers are written, how the current asking price is represented, what acceptance means, and what fields appear in the final receipt. In the base task, the current seller price is interpreted as the last number in an input-required message.

## 5. Required Challenges

### Challenge 1 - Large catalog seller

I generated and committed a synthetic catalog containing 4,000 products. Each product has a SKU, name, description, reserve, category, material, color, style, height, and optional capacity.

The buyer does not need to provide an exact SKU. The recorded natural-language request was:

I need something wooden to keep about 40 books, and it should not be taller than my desk.

The whole catalog is not inserted into the model prompt. The process is:

buyer description
-> harness converts the request into structured criteria
-> Python searches all 4,000 products
-> at most 8 candidates remain
-> harness selects the product and opening price
-> A2A negotiation continues

The log recorded:

[CHALLENGE1 SEARCH] catalog_size=4000 candidate_count=8

The search criteria were:

{"category": "bookshelf", "materials": ["wood"], "colors": [], "styles": [], "max_height_cm": 85, "min_capacity": 35, "max_capacity": 45}

The selected product was:

[CHALLENGE1 SELECTION] sku=ARUN-3001 name='Modern Wood Bookshelf 3001' reserve=114 ask=130

The completed deal was:

[CHALLENGE1 DEAL] {"seller": "Arun Large Catalog Seller", "sku": "ARUN-3001", "item": "Modern Wood Bookshelf 3001", "price": 120, "reserve": 114}

Therefore the final price stayed above reserve.

I also tested an impossible request:

I need a bamboo bookshelf that can hold about 40 books and must be no taller than 50 cm.

The log recorded:

[CHALLENGE1 SEARCH] catalog_size=4000 candidate_count=0 candidate_skus=[]
[CHALLENGE1 NO MATCH]

The seller rejected the request instead of selling an invalid product.

Challenge 1 logs are stored in logs/challenge-1/.

### Challenge 2 - Many shops and one budget

I created 20 A2A shops with different inventories, reserves, and advertised prices.

The buyer first reads all 20 Agent Cards. The recorded run shows:

[CHALLENGE2 CARDS COMPLETE] count=20

The shopping list was lamp, chair, and side table with total budget 450.

The harness selected the shops and item budgets:

lamp -> shop-05 -> budget 100
chair -> shop-04 -> budget 160
side table -> shop-14 -> budget 120

The allocated total was 380, below the global budget of 450.

The three negotiations ran concurrently.

Each negotiation keeps its own taskId, state, item budget, turn number, and seller price. To control context growth, the buyer uses a compact summary limited to 800 characters plus only the two most recent messages.

The recorded context sizes included:

[CHALLENGE2 CONTEXT] item='chair' summary_len=172 recent_messages=1
[CHALLENGE2 CONTEXT] item='lamp' summary_len=170 recent_messages=1
[CHALLENGE2 CONTEXT] item='side table' summary_len=176 recent_messages=1

The purchases completed at:

chair -> shop-04 -> 150
lamp -> shop-05 -> 85
side table -> shop-14 -> 90

The final verification was:

[CHALLENGE2 VERIFY] cards=20 items=3/3 distinct_shops=3 total_spend=325/450 duplicates=0
[CHALLENGE2] PASS

Therefore every requested item was purchased, three different shops were used, no item was purchased twice, and total spending remained below the global budget.

Challenge 2 logs are stored in logs/challenge-2/.

## Final result

Base trade test: 21/21 passed.

Challenge 1: passed with a 4,000-product catalog, candidate narrowing, model-driven selection, reserve enforcement, successful A2A negotiation, and no-match rejection.

Challenge 2: passed with 20 Agent Cards, three concurrent shop negotiations, all 3 items purchased, no duplicates, and total spend 325/450.

Challenge 3 was not included because it is extra credit.