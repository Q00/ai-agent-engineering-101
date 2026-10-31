# Release 1.3.0 code review

- codeQualityStatus: WATCH
- recommendation: APPROVE
- blockers: []
- Review date: 2026-10-21
- Scope: current tracked diff and new production/test files in submissions/25620027/week-07. Read-only source review; no implementation edits, external API calls, SSH, or deployment.
- ULW status: `omo-agent-toolkit ulw-loop status --json` returned ULW_LOOP_PLAN_MISSING; this is the required fallback report path. No separate notepad was supplied/found; worker reports and the parent task handoff supply the decision history.

## Goal and success criteria

Release explicit whole-message opening offers for basic SKUs, preserve exact offer/SKU/task/reserve/signatures, advertise only decide after SKU selection, and state final-price/coupon facts accurately. Persist SDK tasks and negotiation authority across restart in the existing VPS /data mount, while keeping ordinary create_app use nonpersistent. Keep SDK owner semantics and list delegation, close journal entries before terminal publication, and fail closed on corrupt state. Public ordinary/good completed ratings are integer 3 under the requested 3.5 cap; injection incidents remain 1 and honest no-deal remains 3. Selector inputs omit numeric ratings while retaining concrete claims and own price/outcome facts. Ads remain scoped to up to five observed active shops, distinguish ties, and preserve 600-second observation refresh/900-second expiry. Buyer activation, the 21:00 KST deadline, 5,000 observed-call limit and prior cumulative base are outside this patch and must remain operationally unchanged.

## Findings

### CRITICAL
None.

### HIGH
None remaining. Initial persistence tests reproduced taskId-only restart failure: the SDK generated a new context and rejected the signed-sale event after the terminal journal write. `market_a2a/seller_context.py` now resolves the omitted context from the same owner-scoped store before SDK lifecycle setup. Tests retain taskId-only requests and independently pass; this was fixed in production, not hidden by changing the request fixture.

### MEDIUM
1. **Discarded model review still incurs cost and can prevent final reporting.** `market_a2a/strict_review.py:46` discards `proposed`, and all output ratings/text are generated from local facts. Nevertheless `market_a2a/market_buyer.py:210` runs the model review phase, then requires its result at line 227. A model error can therefore prevent a final market result after a completed purchase, even though its proposed rating/prose cannot influence the output. The pre-review trade is already persisted, so local evidence survives. This is unnecessary work under the remove-ai-slops/programming perspectives, with one additional model phase against the observed-call budget. Parent explicitly accepts it as nonblocking technical debt to retain the established Week 05 own-harness lifecycle and class reproducibility during this release; no claim is made that the professor explicitly requires this review call. Follow-up: remove the unused phase or give it a meaningful bounded responsibility, separately from this deployment.

### LOW
None requiring action. Changed-scope lint is clean; existing full-project lint/type findings are described below rather than attributed to this release.

## Skill-perspective check

Ran both required perspectives after reading `/Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.40/skills/remove-ai-slops/SKILL.md` and `programming/SKILL.md`. Reviewed production and tests for deletion-only/request-removal tests, tautologies, implementation-constant mirroring, brittle prompt assertions, untyped escape hatches, unnecessary parsing, and needless abstraction. The MEDIUM finding above violates the unnecessary-work perspective of both skills; no additional material violation was found.

New seller tests execute real A2A routes, enforce amount/SKU/signature/replay boundaries, and script only the external model completion seam. Persistence tests use real SQLite and newly constructed apps. Strict-review tests vary proposed grades and identities and verify actual public outcomes, invalid-offer counting, retained memory and selection inputs. The numeric-rating omission assertion is coupled with retained factual signals and historical-evaluation independence, not a standalone deletion test. Ad tests execute shipped JS against controlled DOM/time/network fixtures and test visible claims, expiry and failure recovery. No new natural-language model-prompt pin was identified. User-visible coupon/ad wording assertions cover the requested truthful output contract. SQLite/schema/offer parsing belongs at the new external persistence/message boundary, not an unnecessary interior normalization layer. Existing seller state machine SIZE_OK explanation is retained; module splits for review/decision contracts have coherent responsibilities. Skill retrospective produced no reusable improvement warranting a skill change; plugin-managed skills were not edited.

## Verified evidence

All paths below are relative to `.omo/evidence/release-1.3.0-20261021/` in this submission.

- `reviewer-tests.log`: independent full offline run, **220 passed in 3.68s**, exit 0. Command recorded in artifact; PYTHONDONTWRITEBYTECODE=1 and pytest cache disabled.
- `reviewer-final-seller.log`: independent rerun after final offer-parser formatting, **21 passed in 1.46s**, including all **8 persistence scenarios**, exit 0.
- `reviewer-source-hashes.json`: SHA-256 binding for inspected tracked changes and all new production/test files; updated after the final parser-only change.
- `reviewer-wrapper/result.log`, `reviewer-wrapper/public-card.json`, `reviewer-wrapper/public-routes.json`: independent local deployment-wrapper run, exit 0; real card version 1.3.0 and all four public routes return 200; isolated temporary SQLite; no model/network call. Earlier local /data-write/version-assertion regression is fixed.
- `persistence/pytest-final.xml`, `persistence/pytest-final.log`, `persistence/REPORT.md`: inspected 8 successful real SQLite/ASGI scenarios, exact signatures and identity after restart, reserve/non-increasing ask, owner-isolated get/list/delete, tombstones, corrupt schema/semantic startup rejection, and explicit failure to migrate old memory-only tasks. Initial failing artifacts remain preserved.
- `lint-changed-final.log`: All checks passed. `store-types-final.log`, `persistence/types-final.log`, `reviews/public-cap-types-final.txt`, and `seller/types-final.log` record scoped successful type checks. `types-all.log` retains four errors in unchanged seller_history.py/test_opening_ask.py/test_seller_settlement.py; `lint-all.log` also retains two unchanged seller_history findings and the subsequently fixed offer-parser lint finding. Full-project static checks must not be described as universally clean.
- Inspected ads scenario JSONs, executed their tests in the independent full run, and read actual JS/HTML: expired/unverified/error feeds remove promotional claims; tied observed minima use 공동최저; no new competitor probing route was added.
- Direct diff review confirms create_app opens SQLite only when AX_SELLER_STATE_DB is set; VPS wrapper defaults it to /data/seller-state.sqlite3 on the existing persistent volume. Task serialization delegates owner/list behavior to the pinned SDK. Journal commits precede quote/receipt/rejection/cancellation publication.

## Limits and release handoff

Approval covers the inspected code and local behavior. It does not claim completion of the official live 25 scenarios, deployed visual/browser QA, remote container replacement, current usage-base verification, or public smoke tests; those remain parent release work. Persistence tests instantiate new apps/stores and do not simulate process kill or concurrent old/new workers. The store is suitable for the current single-process deployment, not a multi-worker shared-cache design. SQLite I/O failure-before-publication behavior was inspected in code, not separately fault-injected. A crash after terminal journaling and before receipt delivery intentionally fails closed and may leave a buyer without a retrievable completed receipt; no exactly-once delivery or zero-downtime migration claim is approved. Existing in-memory tasks cannot be migrated into the new database. Operational buyer OFF/deadline/cumulative usage settings must be checked by the authorized deployer.


## Follow-up: 1.3.1 opening-offer settlement correction

Follow-up decision: **APPROVE**, codeQualityStatus **WATCH**, blockers **[]**. The new correction itself has no remaining findings; WATCH retains the accepted MEDIUM model-review debt above.

The earlier code review missed that a model could quote instead of accepting a feasible explicit opening offer. Real public evidence `public-restart-finish.log` shows version 1.3.0 receiving `offer lamp 100` and returning INPUT_REQUIRED with ask 100. The prior restart portion completed the same existing task/context at signed 100 and rejected replay, but did not establish one-shot offer completion. The reproduction remains in `opening-settlement-regression-before.log` (three failing first-offer cases). This release must use the updated **1.3.1** image; prior 1.3.0 approval does not establish compliance with this newly demonstrated outcome.

Inspected `market_a2a/seller_policy.py:122` opening_settlement_feedback and its invocation in seller_turn.commit. After retrieved-SKU validation, only an opening turn with matching explicit SKU and actual offer at/above reserve rejects a model quote, returning required_action=accept and the exact offered amount. Existing price-error feedback takes priority. The model still has to issue an actual accept call; no receipt, quote publication, or negotiation state mutation occurs from this feedback. Acceptance retains exact amount/SKU guards. Below-reserve offers still quote, ordinary item requests still open with quotes, and existing selected-SKU settlement behavior is preserved.

The updated real ASGI regression supplies a rejected quote before a valid accept and checks exact Ed25519-signed receipts at 100 and 125, both accepted grammar forms at 100, selected SKU/task, and terminal replay rejection. The 125 case distinguishes the offered amount from reserve and opening ceiling. Initially adding this action caused seven scripted calls to exceed the existing six-call loop; `opening-settlement-offline.log` retains that failure. The redundant pre-search accept was removed from this scenario; the production loop limit was not increased. Final parent full-suite artifact `opening-settlement-offline-final.log` records 221 passed. After the final policy-function extraction, independent `reviewer-opening-settlement-final.log` records **42 passed**, including all **8 persistence tests**. Source SHA-256 bindings are in `reviewer-opening-settlement-hashes.json`.

`opening-settlement-lint-clean.log` reports All checks passed with the existing invalid SIZE_OK noqa warning still visible; `opening-settlement-types-clean.log` reports zero errors/warnings/notes. The earlier new C901 complexity failure was resolved by locating commercial-policy feedback with the existing policy functions, without suppressing C901. Both required skill perspectives were applied again: the regression exercises observable completion rather than prompt wording or a constant, and the policy extraction is a coherent policy responsibility rather than speculative abstraction. No new medium/high complexity finding remains.

This follow-up approves inspected code and local scenarios only. Updated-image deployment, official live attempt 2 and a live first-offer completion smoke must be completed and reported by the parent using their actual artifacts; they are not claimed here.
