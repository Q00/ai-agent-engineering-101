# Seller implementation evidence

Scope: seller.py SDK lifecycle; seller_turn.py bounded model negotiation; test_current_seller.py HTTP scenarios. No paid API calls or commits. No active attempt was returned by `omo-agent-toolkit ulw-loop status --json` at the submission directory. This directory is the project-local evidence fallback.

## Executed scenarios

Invocation for all scenarios: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests/test_current_seller.py -q -s`.
Captured artifact: `pytest-final.log`; binary result: 10 passed, exit 0. The scenario uses the real A2A SDK routes, real custom Harness, real OpenAI client serialization and a finite HTTP model responder. It does not validate live model behavior; the root agent owns official paid trade/challenge runs.

| Scenario | Observable assertion |
|---|---|
| Root and mounted shop discovery (2) | Card endpoint equals supplied base URL |
| Below-reserve quote, below-reserve acceptance, wrong incoming acceptance price, SKU change (4) | Invalid decision is not committed; corrected quote ends 120, same task/context, no artifacts, script consumed |
| Acceptance of incoming 100 | COMPLETED and receipt item=lamp, sku=lamp, price=100; opening metadata selects lamp |
| Followup after acceptance and cancellation (2) | JSON-RPC error, no successful result |
| Unknown product rejection | REJECTED, no receipt |

## Static gates

- `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m ruff check market_a2a/seller.py market_a2a/seller_turn.py tests/test_current_seller.py` → `ruff.log`, All checks passed.
- Adjacent `bin/basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python market_a2a/seller.py market_a2a/seller_turn.py tests/test_current_seller.py` → `types-final.log`, 0 errors / warnings / notes.
- `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.30/skills/programming/scripts/python/check-no-excuse-rules.py market_a2a/seller.py market_a2a/seller_turn.py tests/test_current_seller.py` → `no-excuse.log`, no violations in 3 files.

## Interface and review

`create_app(base_url: str, harness: Harness, catalog: Catalog, name: str) -> FastAPI` is the caller-required four-argument factory; changing that contract would broaden scope. Card URL is supplied verbatim; local routes support mounting. `search_catalog(query)` exposes at most six candidate products; `decide(action,sku,price,reason)` is the only commit tool. Each numeric incoming offer is bounded to nine decimal digits, not inferred from arbitrary narrative numbers. Quote and acceptance guards reject below-reserve prices; acceptance requires exact incoming numeric offer and an existing selected item. Quote message ends with the asking price and metadata contains sku/item/name. Receipt data contains seller/item/sku/price; item is product kind for basic test compatibility. The SDK retains history; the model only receives selected product, ask, rounds, latest bounded buyer input and offer.

Single responsibilities: SDK lifecycle, negotiation turn, wire scenarios. External tools parse through Pydantic; action matching is exhaustive. No cast, Any, type ignores or broad exception catches. Mutable maps are intentional state with SDK-task locks. Tests exercise actual wire-visible outcomes with no paid calls. Logging uses the supplied harness sink and records bounded task/candidate/guard/receipt data, no credentials. The programming skill is provider-managed and no skill changes were made; no reusable defect in its instructions was established.

Pure LOC: seller.py=94, seller_turn.py=247, tests/test_current_seller.py=235. Seller turn/tests are in the 200–250 warning band; extract the tool declarations before substantial additions. Previously failed tooling attempts remain preserved in types.log/types-current.log; only types-final.log is claimed passing.
