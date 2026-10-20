# Week 07 code quality review

- codeQualityStatus: BLOCK
- recommendation: REQUEST_CHANGES
- Review base: 80b6649; working tree tracked diff plus new market_buyer.py, memory.py, receipts.py, tests, and pyproject.toml.
- Scope: Week07 market buyer and signed seller integration; preserve Week05 model tool loop, order/budget/task integrity, stable-name memory, canonical Ed25519 signature, and private-key secrecy.
- ULW status returned ULW_LOOP_PLAN_MISSING; fallback report path is submissions/25620027/week-07/.omo/evidence/w07-code-review.md. No notepad path was supplied or found in submission .omo.

## CRITICAL
None.

## HIGH
1. **Market orders can accept a seller-substituted product.** `market_a2a/market_buyer.py:172` delegates to the existing buyer and at line 187 records the returned purchase without enforcing the original order item. In `market_a2a/buyer.py:143`, seller-controlled metadata replaces the authorized item. The receipt boundary therefore checks against the seller's substituted item. This behavior predates this diff in the shared buyer, but the new market path exposes it for an exact-item market order. A seller advertising metadata `item=chair` and issuing a chair receipt can complete a request for lamp at 120. The kit subsequently refuses to sign the mismatched order, and persisted own-trade memory describes it as completed. Enforce the market order's product identity before authorizing an offer (or otherwise ensure the market path cannot record/return it as a successful authorized purchase), preserving legitimate descriptive Week06 shopping behavior. Add an adversarial metadata-substitution regression.

Independent offline reproduction used the real `buy`, `ShopTransport` from tests/market_fakes.py, a MockTransport wrapper adding `task.metadata={"item":"chair"}` to responses, `peer.receipt["item"]="chair"`, and a scripted offer 120 for requested lamp. Actual output:

```json
{"requested_item":"lamp","completed":true,"receipt_item":"chair","offers":[[{"text":"I want to buy lamp"}],[{"text":"120"}]]}
```

## MEDIUM
None.

## LOW
None.

## Verification and skill perspectives

Loaded and consulted both managed skills: `/Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.35/skills/remove-ai-slops/SKILL.md` and `programming/SKILL.md`. Applied their test-overfit, boundary parsing, needless abstraction, and typed-interface review criteria. No deletion-only tests, removal-only assertions, brittle prompt-text assertions, tautological tests, untyped escape hatches, or unnecessary production extraction/normalization were found in the new scope. Exact canonical byte assertions test the external cryptographic protocol, not incidental implementation constants. Registry, model tool, memory-file, key-file, and receipt parsing serve actual boundaries. The HIGH finding violates the programming trust-boundary perspective; no distinct slop finding warrants requesting refactoring. Skill reflection found no reusable managed-skill change to make.

Independent command in submission directory:

`PYTHONDONTWRITEBYTECODE=1 /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests -q -p no:cacheprovider`

Result: **34 passed in 1.18s**, exit 0. `git diff --check` passed. Inspected `.omo/evidence/w07-signing.json` and `.omo/evidence/w07-market-buyer.json`; all five signing evidence hashes match current files. Tests cover real A2A seller routes with scripted completions, cryptographic tampering, unchanged receipt payload, hard budget rejection, task mismatch rejection, durable stable-name memory, and CLI path isolation. The pending task-id change is present and tested.

No live API calls, official market test, or market challenges were run by this reviewer. Scripted model actions do not establish live shop-selection, negotiation, review quality, or resistance to prompt injection. No completion claim is made for the whole Week07 assignment. Production source was not edited.

## Blockers

- Fix exact market order product authorization against malicious seller metadata and demonstrate the rejection with an offline regression.

## Follow-up verification — HIGH resolved (2026-10-20)

The initial BLOCK finding above is retained as review history. **Current verdict: codeQualityStatus CLEAR; recommendation APPROVE; blockers: [].** This supersedes the initial verdict for the reviewed implementation scope.

Inspected the final `market_a2a/buyer.py` diff, market caller, `tests/test_buyer_order.py`, and `.omo/evidence/w07-market-buyer-item-guard.json`. `market_buyer.py:173` now explicitly supplies `BuyerOrder(..., exact_item=True)`. `buy_order` rejects either initial message metadata or task metadata naming a different item before the negotiation loop can submit a price. When metadata is absent, authorization remains the requested item, so the existing receipt check rejects a substituted receipt. The market boundary catches the typed failure and records an unsuccessful trade. The legacy `buy` entry point delegates with the default descriptive-item behavior, preserving Week06 callers.

The added regression drives the actual market path through the HTTP fake, proves only the initial item request is sent, checks null receipt and failed persisted observation, and separately confirms a descriptive legacy request still completes. These tests observe behavior rather than implementation text; the typed order is a meaningful shared policy boundary, not unnecessary abstraction. Both skill perspectives remain satisfied after repair.

Independent full snapshot verification repeated because production code changed:

`PYTHONDONTWRITEBYTECODE=1 /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests -q -p no:cacheprovider`

Actual result: **36 passed in 0.93s**, exit 0. `git diff --check` also passed. No remaining CRITICAL, HIGH, MEDIUM, or LOW findings in this review's bounded scope. Live model and official market-test limits above remain; this approval does not certify full assignment completion or the pending live evaluation.

## Final evidence interpretation check — official run (2026-10-20)

Current bounded code-review verdict remains **CLEAR / APPROVE**, blockers: []. Read REPORT.md, w07-validation.md, test-report.json, market-test.log, structural-after-25.log, and the preserved validation-attempt-1 artifacts. No tests or API calls were executed for this follow-up.

Verified 25/25 passing result rows; recorded kit SHA-256 equals the current scripts/market_test_week07.py bytes. Current report and market-test log exactly match their preserved first-attempt copies. Seller signatures pass 3/3, deals show seller price 100 and buyer price 120, below-reserve attempts stay at budget 230 with null receipts, and reviews selection chooses South 3/3. The snapshot contains no PEM file. Memory counts are Kit Shop 6 and Kit Shop South 3. Parsed usage logs independently total 116 MODEL_USAGE events, input 75,005/output 3,980 tokens, and zero MODEL_RETRY entries.

The reports appropriately distinguish implementation/official-test success from full assignment completion. Actual class-market Round 0/1/2 participation, its ledger log, and required challenges 1/2 are explicitly unfinished. Structural output contains precisely one failure: logs/market-kit.log missing or lacking a ledger entry. Both reports disclose all three normal below-reserve outcomes received one-star reviews and repeated 230 offers; they do not claim fair reputation or optimal negotiation from the 25 passing checks. No misleading completion or evidence-consistency finding remains. The earlier official-test verification limit is now superseded by this artifact inspection; actual class-market outcomes remain unverified and unperformed as reported.
