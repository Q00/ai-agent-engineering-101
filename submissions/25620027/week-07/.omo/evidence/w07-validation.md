# Week07 implementation validation — 2026-10-20

Status: implementation and offline checks complete; official model-backed 25-case run pending.
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
