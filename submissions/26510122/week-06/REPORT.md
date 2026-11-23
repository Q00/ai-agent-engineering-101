# Week 06: A2A seller and buyer

## 1. Harness and reproduction

Both agents use the existing Codex CLI login with `gpt-5.6-luna`. Every negotiation decision invokes `codex exec --ephemeral --ignore-user-config --ignore-rules --strict-config --skip-git-repo-check --sandbox read-only --cd <empty temporary directory> --model gpt-5.6-luna --config developer_instructions=<role instruction> --output-schema <schema.json> --output-last-message <answer.json> <prompt JSON>`. The temporary directory keeps project instructions out of the model's context. The CLI has no temperature setting in this run, so temperature is unset. Python dependencies are pinned in `requirements.txt`; no API key is stored or needed.

```bash
cd submissions/26510122/week-06
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python ../../../scripts/trade_test_week06.py .
.venv/bin/python challenge1.py
.venv/bin/python challenge2.py
python3 ../../../scripts/check_week06.py .
```

`agent.json` contains the exact seller and buyer commands the trade test runs. The final official test report records **21/21 passed**, with three model-driven repeats for each price decision case. The first 21/21 run is preserved in `logs/first-pass/`; the root `test-report.json` and `logs/trade-test.log` come from the rerun after the challenge extensions.

## 2. Decisions, messages, and price limits

The seller exposes a JSON-RPC A2A 1.0 interface and Agent Card. A first `SendMessage` starts a task; the seller finds the named product, gives the model its reserve and buyer message, and asks it for an opening price. Follow-ups use the same taskId. The model sees the latest numeric buyer offer, current ask, reserve, and local offer history, then chooses `deal`, `counter`, or `reject`. It returns strict JSON with `action` and nullable integer `price`. The A2A server maps those choices to `TASK_STATE_COMPLETED` with a data-Part receipt, `TASK_STATE_INPUT_REQUIRED`, or `TASK_STATE_REJECTED`. Code checks that every ask is at least the reserve and never executes a model-requested deal below reserve; such corrections are logged as `price_guard`.

The buyer first reads `/.well-known/agent-card.json`, selects its JSON-RPC 1.0 URL, then sends every request with `A2A-Version: 1.0`. It keeps the taskId for later turns. The model sees the private budget, current seller message, card skills, and negotiation history; it chooses `offer` with a price or `walk`. Code clamps offers to the budget and records any correction. A walk stops the buyer without creating a deal. The initial buyer and seller decision prompts and returned JSON are visible in `logs/seller-under-test.log` and `logs/buyer-under-test-*.log` as `harness_input`/`harness_answer`; the full instruction and schema are in `harness.py`.

## 3. Failed negotiations and iteration

Neither official trade-test attempt failed: both the first run and the post-extension rerun finished 21/21. I therefore cannot cite a failed test line or claim a repair that did not happen. There was a visible **negotiation failure mode** even though the safety test passed: the fixed buyer kept offering 230 for a bookshelf whose seller reserve was 260. The seller answered `Counteroffer: 261` repeatedly ([trade-test.log, lines 49–55](logs/trade-test.log#L49)); another run eventually ended `TASK_STATE_REJECTED` ([line 77](logs/trade-test.log#L77)). On the buyer side, the model sent five 230 offers in one no-overlap case while no deal formed ([lines 113–115](logs/trade-test.log#L113)). This exposed the cost of not detecting stalled bargaining early. After the first pass, I added a compact per-task summary for the multi-shop buyer, so long parallel conversations do not resend all prior turns; the core trade-test buyer still has an eight-turn ceiling and may make unnecessary offers. The final rerun checks that these extensions did not break the protocol or price rules.

## 4. What A2A settled and what it left open

A2A gave independent buyer and seller programs a discoverable Agent Card, a common JSON-RPC `SendMessage` interface, a taskId for continuing the same negotiation, and terminal states and receipt artifacts. The protocol rejected an extra message after a completed lamp sale with error `-32004` ([trade-test.log, lines 16–19](logs/trade-test.log#L16)). The peers still had to agree on the application language: the default seller asks with a numeric price at the end of its text, the buyer replies with one whole-number offer, and the receipt stores a numeric `price` in a data Part. A2A transports these messages but does not decide which number is the current offer or enforce either private limit. Those rules are in the prompts and the local guards.

## 5. Required challenges

**Large catalog.** `generate_catalog.py` deterministically creates 2,346 products with descriptions and reserves. `catalog.py` normalizes synonyms and filters by category, material, book capacity, and height; it ranks candidates in code and gives only 12 to the harness. The harness chooses a product ID, then a separate seller decision sets its ask. For “something wooden to keep about 40 books, not taller than my desk,” the log shows `total: 2346` and the 12 candidates ([seller.log, line 1](logs/challenge-1/attempt-01/seller.log#L1)), selection of `shelf-wood-40-50-black` ([line 4](logs/challenge-1/attempt-01/seller.log#L4)), reserve 155 and ask 180 ([line 7](logs/challenge-1/attempt-01/seller.log#L7)), followed by a sale at 180 ([line 10](logs/challenge-1/attempt-01/seller.log#L10)). The selected product is wood, holds 40 books, and is 50 cm tall. The shortlist is an engineering tradeoff: it keeps model context small, but a retrieval error could hide the right product before the model sees it.

**Many shops.** `challenge2.py` starts 20 local A2A shops with different Agent Cards and reserve tables. `multi_buyer.py` reads their skills and indicative prices, asks the harness to choose a shop and per-item allocation, then starts the three item negotiations concurrently. Allocations reserve money for unpurchased items; the code checks receipts against each allocation and the shared total, and stores purchases by item key to avoid duplicates. Per task, the model receives its taskId, latest seller message, previous offer, offer count, and only two recent exchanges; older messages are not replayed. The log records discovery of 20 cards ([buyer.log, line 21](logs/challenge-2/attempt-01/buyer.log#L21)), three allocations ([lines 24–30](logs/challenge-2/attempt-01/buyer.log#L24)), compact task summaries for chair and bookshelf ([lines 38–42](logs/challenge-2/attempt-01/buyer.log#L38)), and final purchases of lamp, chair, and bookshelf once each for **560 of 650** ([line 63](logs/challenge-2/attempt-01/buyer.log#L63)). This run uses self-hosted shop copies; cross-harness trading with classmates was not attempted.
