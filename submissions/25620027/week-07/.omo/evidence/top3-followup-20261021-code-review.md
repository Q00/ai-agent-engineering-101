# Seller 1.2.1 code review

codeQualityStatus: WATCH
recommendation: APPROVE
blockers: []
Scope: working-tree diff of market_a2a/{catalog,seller_turn,seller,seller_policy}.py, assets/seller-ad.html, untracked tests/test_search_shortlist.py; separately authorized deploy/vps/guard.py deadline change.

## Findings
CRITICAL: None.
HIGH: None.
MEDIUM: tests/test_search_shortlist.py:181–195 pins release version and prose fragments such as "numeric offer" and "Dated". These assert wording rather than independently verifying truthfulness; legitimate wording edits fail them while incorrect prices can remain green. Prefer manual card inspection and behavioral receipt/price assertions. The signer-conditioned claim does have meaningful branch coverage. This is nonblocking test maintenance debt, not a demonstrated production failure.
LOW: None.

## Correctness and scope
Catalog.search applies both its existing query constraints and the optional original-request predicate before ranking/truncation, fixing candidate starvation. Existing callers retain their default behavior. The strict full-match basic-request rule cannot consume appended descriptors; descriptive requests continue through the prior compatible predicate. This does not expand the existing predicate's understanding of colors, arbitrary sizing phrases, or capacity language; tests with simplified model queries do not establish comprehensive natural-language correctness.

The model still issues search_catalog and decide; code only constrains retrieved candidates and validates actions. Added quote bounds agree with existing reserve/current-ask/opening-ceiling guards. Acceptance still requires actual incoming offer equality. Receipt signing, settlement, coupon accounting and state transitions are unchanged. Card signing claim is conditional on configured signer, and comparison wording avoids claiming globally lowest prices. Basic minimum values remain configured marketing facts rather than dynamically derived for arbitrary catalogs.

Guard change consistently moves deadline from 09:00 to 21:00 +09:00 and retains CALL_LIMIT=5000; deployment/process state not reviewed.

## Evidence inspected
- implementation/checks.json under .omo/evidence/top3-followup-20261021/
- implementation/pytest-pass.txt and pytest-pass.xml: 178 passed, includes shortlist, settlement and signed-receipt cases.
- implementation/ruff-pass.txt: all checks passed.
- implementation/types-pass.txt: 0 errors/warnings.
- Earlier types-final.txt contains a now-corrected implicit-string-concatenation error; it is not the passing artifact. Current regex is a single literal.
- Full current source/diff and new test file inspected directly. No test/model/API/process execution was performed by this reviewer; test claims above describe inspected executor artifacts, not an independent rerun. No live ranking or conversion improvement is established.

## Skill-perspective check
Loaded programming/SKILL.md, its Python README, and remove-ai-slops/SKILL.md and applied their test relevance/overfit, boundary, typing and abstraction criteria. Both perspectives identify the MEDIUM prose/version test pin above. No deletion-only tests, untyped escape hatches, or unnecessary production normalization/extraction were introduced. The predicate seam is justified by the pre-limit requirement; basic-request full matching is narrowly scoped. No other violations found in this diff. Read-only review did not run cleanup/refactoring instructions. No reusable skill change identified; managed skill files unchanged.

## Review metadata
ulw-loop status --json reports ULW_LOOP_PLAN_MISSING from repository and submission, so fallback evidence path used. Goal/success criteria supplied in delegation. No separate notepad path supplied or found in scoped file discovery.
