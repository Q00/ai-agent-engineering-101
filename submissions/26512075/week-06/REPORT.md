# Week 06 — A2A lamp shop

## 1. Harness

Seller and buyer both call a Python harness. The harness is Codex CLI with model `gpt-6.1-sol`.

Exact command:

```bash
codex exec -m gpt-6.1-sol --skip-git-repo-check --sandbox read-only --output-last-message {output} -
```

The prompt is written to stdin. Codex writes the final message to `{output}`. The process runs in an empty temporary directory so it does not pick up project instructions. `HARNESS_BACKEND=openai` switches the same prompt to the OpenAI Responses API; the default is Codex.

`agent.json`:

```json
{
  "harness": "Codex CLI (gpt-6.1-sol) for seller and buyer",
  "seller": {"cmd": "python seller.py --port {port}"},
  "buyer": {"cmd": "npx tsx src/buyer.ts --url {url} --item {item} --budget {budget}"}
}
```

## 2. How each agent decides

The harness returns one JSON object.

- Seller: `{"action":"offer"|"accept"|"reject","price":number,"parsed_offer":number}`
- Buyer: `{"action":"offer"|"accept"|"reject","price":number}`
- Catalog pick: `{"action":"select_item"|"reject","itemId":string}`
- Shop pick: `{"action":"select_shops","shops":[string]}`

Reserve and budget are in the prompt and enforced in code. `checkDecision` rejects a seller price below `request.limit` and a buyer price above `request.limit`. Accept and offer require a positive integer `price`. A missing `price` is filled from `ask` before the check. The model cannot sell below reserve or pay above budget even if the prompt is ignored.

## 3. What failed

`INVALID_PARAMS` (`-32602`) on the first `SendMessage`. The JS SDK sent `role: "user"` and parts with `kind`. The Python server expects proto JSON: `ROLE_USER` and `{"text": "..."}`. Buyer `send` was rewritten as raw `fetch` with the `A2A-Version: 1.0` header.

`TypeError: decide(request) must return a JSON-serializable dict`. In `decide`, the read of `--output-last-message` was indented under `if result.returncode:`, after `raise`. A successful Codex run returned `None`. The read and `_decode` were moved to the success path.

`offer or acceptance needs a positive integer price` with `decision err: undefined`. The model returned `ask` or omitted `price`. `normalize` copies `ask` to `price` and coerces numeric strings; `checkDecision` now uses that normalized object.

`Completed task did not include a numeric receipt price`. The receipt part was `{"data": {...}}`, not `content.$case === "data"`, and `item` was `blue bamboo lamp 00` while the buyer compared to `lamp`. `receiptPrice` accepts both part shapes and matches on name inclusion or `itemId`.

Earlier local errors, not protocol failures: `require is not defined` in an ESM file (use `import`), `ECONNREFUSED 127.0.0.1:9999` (seller not running), `No module named 'a2a'` (`pip install "a2a-sdk[http-server]"`).

## 4. What A2A fixed, and what it did not

A2A fixed task identity, state, and the shape of a completed deal. Both sides share `taskId` and `contextId`, the state enum (`INPUT_REQUIRED`, `COMPLETED`, `REJECTED`), the Agent Card, and the `A2A-Version: 1.0` header. A finished task returns an error instead of accepting another offer.

A2A did not settle how a price is written or what accept looks like. The trade test reads the ask as the last number in an `INPUT_REQUIRED` message, so the seller puts that number last and also sets `metadata.ask`. The receipt is a data Part with a numeric `price`. Accept means `TASK_STATE_COMPLETED` plus that artifact, not a magic word. Both sides also agreed that the buyer's offer is the integer in the follow-up text, and that comparison prices in a long message are not the offer.

## 5. Challenges

Challenge 1. The seller does not paste the catalog. `catalog.py` shortlists candidates in code from the buyer's description, then the harness sees only that list. From `logs/challenge-1`:

```text
QUERY something wooden to keep about 40 books, not taller than my desk
CHOSEN bookshelf-wood-blue-11 reserve=282 capacity=40 height=70
ASK 340.0
```

The shortlist is a handful of ids, not thousands of rows. Selling the wrong item, or asking below that item's reserve, is rejected in code.

Challenge 2. The buyer reads skills on the Agent Cards, asks the harness for `select_shops`, and negotiates with several shops. It keeps one record per shop: taskId, last ask, offers so far, and whether that shop is closed. The running summary is what the next prompt sees, not the full transcript. It stops when every list item is bought once and the total is within budget.

Trade test: `21/21 passed` (`test-report.json`, kit v1, 2026-10-18).
