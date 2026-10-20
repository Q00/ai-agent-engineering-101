# Opening ask implementation evidence

## Scope and status

Candidate worktree: `ai-agent-engineering-101-week-07-opening-ask`, branch `codex/week07-opening-ask`, starting source `9db1fa2`.

Implemented only the initial seller asking-price policy. The model still chooses the SKU, integer price and action through the existing harness. Search results carry the exact opening maximum next to the product. The code accepts an opening quote only between reserve and `(reserve * 105 + 99) // 100`, inclusive. Above-limit attempts return typed feedback to the model before any task event or negotiation state mutation. The original below-reserve guard remains effective. Later quotes and actual incoming offers above the opening ceiling remain allowed by the original negotiation rules.

Implementation changed `market_a2a/seller_policy.py` and `market_a2a/seller_turn.py`. `Negotiation` is the same frozen value object, relocated into the seller policy module to keep the transport module within its existing strict size limit. Buyer policy, catalog reserves, model, signature implementation, metadata and protocol are unchanged.

This worker did not execute model APIs, market join, tunnel commands, live process changes, credential copying, commits, or pushes. The parent owns official 25-case model verification and any subsequently authorized deployment. Offline success below does not itself claim official 25-case success or improved live conversion.

## Invocations and binary observations

All commands ran from the candidate `submissions/25620027/week-07`.

Tool directory:
`/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin`

| Scenario | Invocation | Binary observable | Captured artifact |
|---|---|---|---|
| Reproduce old high opening behavior before implementation | `<tool-dir>/python -m pytest -q tests/test_opening_ask.py tests/test_signed_seller.py` | Exit 1; all 9 selected cases fail because old code publishes the high initial ask | `opening-ask-red.log` |
| Full offline suite after implementation | `<tool-dir>/python -m pytest -q` | Exit 0; 45 passed in 0.86s | `opening-ask-offline-pass.log` |
| Strict Ruff ALL | `<tool-dir>/ruff check .` | Exit 0; All checks passed | `opening-ask-lint-pass.log` |
| Strict basedpyright | `<tool-dir>/basedpyright --pythonpath <tool-dir>/python` | Exit 0; 0 errors, 0 warnings, 0 notes | `opening-ask-types-final.log` |
| Format | `<tool-dir>/ruff format --check market_a2a/seller_policy.py market_a2a/seller_turn.py tests/test_opening_ask.py tests/test_signed_seller.py` | Exit 0; 4 files already formatted | `opening-ask-format.log` |
| Skill static rules | `<tool-dir>/python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.35/skills/programming/scripts/python/check-no-excuse-rules.py market_a2a/seller_policy.py market_a2a/seller_turn.py tests/test_opening_ask.py tests/test_signed_seller.py` | Exit 0; no violations in 4 files | `opening-ask-no-excuse-final.log` |

All artifact paths in this table are relative to this evidence directory. Intermediate lint/type/size failures are preserved in the other `opening-ask-*.log` files. No baseline logs were edited.

## Behavioral coverage

`tests/test_opening_ask.py` adds nine cases:

- Real ASGI A2A route and existing harness tool loop, with only external model completion replaced: invalid high opening, invalid below-reserve opening, then the model's corrected valid price. Seven cases cover lamp 100 and 105, chair 180 and 189, bookshelf 273, reserve 101 ceiling 107, and reserve 1 ceiling 2. The buyer sees only the final corrected price. The machine-readable search ceiling and feedback bounds are checked.
- Direct tool commit with opening 99 or 106 against lamp reserve 100: rejected nonterminal feedback; no queued status/artifact event; empty negotiation map; no closed task.

`tests/test_signed_seller.py` retains signed and unsigned real A2A lifecycle scenarios. The invalid initial 120 now receives model correction to 105. A later below-reserve acceptance still fails, a later quote 120 remains valid, and an actual incoming 120 is accepted and cryptographically verified. Terminal replay still fails.

## Self-review

Each file retains one responsibility: seller decision policy, A2A turn orchestration, opening-price behavior tests, signed seller lifecycle tests. Price feedback crosses the model boundary as a frozen typed object. Action matching is exhaustive. No Any, cast, ignore, broad catch, new dependency, alternate model or bypassed validation was introduced. Prompt prose is not asserted.

Pure LOC: seller_policy 110; seller_turn 246; test_opening_ask 157; test_signed_seller 174. The transport module is in the warning band; any further expansion should first split its task transition responsibility.

Managed `omo:programming` and its Python/data/async/network/logging references were used. The size finding triggered a narrowly scoped policy extraction following `omo:refactor`; no broad rewrite was performed. No user-owned skill had reusable evidence warranting an automatic skill edit.

## Source fingerprints at handoff

| File | SHA-256 |
|---|---|
| market_a2a/seller_policy.py | b82bc6b463bff168ce3e24d621b6344f4241add06c1cdd0445daa27d67d8d631 |
| market_a2a/seller_turn.py | 3ecf93712fcba63eeab3578b37fed3ba60cac581ddb7162694ac89a2daf3c4bf |
| tests/test_opening_ask.py | 4bea1ee55c556a69bb4d714da16cd9c08cc0e3f1e9e466f743541198452bd9be |
| tests/test_signed_seller.py | c62f46226f9df6edcd896ce912cb0e3b69d7bd5d9cd5eb839ca784967d6261b5 |
