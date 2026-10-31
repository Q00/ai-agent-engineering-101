# Week07 implementation validation — 2026-10-20

Status: implementation and offline checks complete; first official model-backed run passed 25/25. Actual class-market participation remains unperformed.
Official source: `7a2dfb159c199ac257772176662d45161914c08e`.

## Observed offline verification

- `python -m pytest -q`: 36 passed, including canonical signatures, tampering/wrong keys, real ASGI seller lifecycle, budget guard, raw receipt/task preservation, stable-name memory, atomic-write failure, HTTP failure observations and CLI absolute paths.
- `ruff check .` and `ruff format --check .`: pass, 25 Python files formatted.
- `basedpyright --pythonpath <cached-python>`: 0 errors, 0 warnings, strict all mode.
- Interpreter/tools: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/`.
- Raw outputs: [tests](../../logs/offline-tests-20261020.log), [lint](../../logs/lint-20261020.log), [types](../../logs/typecheck-20261020.log).

## Independent review and correction

A read-only review reproduced a pre-existing buyer gap: seller metadata could replace
a requested lamp with a chair. The market path now requires the original item and
rejects a mismatch before the first price offer. The legacy descriptive buyer stays
compatible. Two new regressions observe no price offers for a substituted product
and successful legacy descriptive checkout. The original finding and follow-up
review are preserved in [code review](w07-code-review.md).

## Runtime and data boundary

The inherited custom Week05 tool loop and fixed OpenAI
`gpt-4.1-nano-2025-04-14` drive shop selection, offers and review.
Local code enforces the budget and validates data; the market kit adds buyer
signatures after checking the seller receipt. Seller private keys stay ignored.
Only assignment shop cards, product/price/budget, reviews and negotiation state
are sent to the model. The private environment was checked for presence without
printing credential values; its API host is api.openai.com.

The user's current request explicitly includes implementing both features and
running the official 25 checks. No public-market join, deployment, challenge run
or additional student messaging is part of this run. A real market ledger log and
the two required challenges remain separate assignment work.

## Official model-backed run — first attempt

Command from repository root, with umask 077 and pipefail:

```bash
UV_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
  submissions/25620027/week-07/.venv/bin/python \
  scripts/market_test_week07.py submissions/25620027/week-07 \
  | tee submissions/25620027/week-07/logs/market-test-console-attempt-1.log
```

- Invocation exited 0: **25/25 PASS**. Implementation checkpoint: `efbc5c7`.
- Seller lamp receipts were 100 in all three runs; all three signatures verified.
- Buyer lamp receipts were 120 in all three deal runs; all three 230-budget bookshelf runs had no over-budget offers and no receipt.
- Reviews test selected only the good-review South shop in 3/3 runs; North received no POSTs.
- Observed model usage: 116 calls, 75,005 input and 3,980 output tokens; zero logged MODEL_RETRY entries. No invoice total is inferred.
- Persistent own memory contains Kit Shop 6 and Kit Shop South 3 trades.
- The current `test-report.json` records 25 passing rows and matches the unmodified official kit SHA-256.
- Raw report, seller/buyer logs, registry inputs and memory snapshot are preserved in [validation-attempt-1](../../logs/validation-attempt-1/). No private key is in that snapshot.
- Important observation: ordinary below-reserve no-deals received one-star reviews three times. Repeated 230 offers also occurred. Passing checks does not establish fair reputation, optimal negotiation or general reliability.

## Full submission structural check

The checker accepted the current 25-case report, source syntax, command placeholders,
market-test log, report and key exclusion. It exited 1 only because no actual
class-market ledger entry exists in logs/market-kit.log. This is an outstanding
assignment requirement, not a failed model-backed case. See
[raw structural output](../../logs/structural-after-25.log). No ledger log was fabricated.
