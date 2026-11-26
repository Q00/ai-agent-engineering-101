# Week 06 code-quality / requirements review

- codeQualityStatus: WATCH
- recommendation: APPROVE
- reportPath: submissions/25620027/week-06/.omo/evidence/week06-requirements-code-review.md
- blockers: []
- Scope: report corrections and changes from 2dd0dab through 8bb9fc7; followup actual execution artifacts. Read-only source review; only review artifacts written.
- Detailed evidence: [REQUIREMENTS_AUDIT.md](week06-report-followup-20261014/REQUIREMENTS_AUDIT.md).
- ULW routing: status returned ULW_LOOP_PLAN_MISSING, so fallback evidence path used. No separate notepad was supplied; official README, report, commit diffs, invocation records, and raw logs supplied context.

## CRITICAL

None.

## HIGH

None remaining. Former H1 was the failed ordinary C2 attempt-5 (4/5 purchased, 1506/2200). The revised planning instructions in 8bb9fc7 now have observed general C2 success in attempt-6: 5/5 purchased for 1757/2200 after one failed checkout and two plans. The previous failure is retained, not rewritten. This resolves the missing-current-success blocker; it does not prove repeatability or universal model compliance.

## MEDIUM

- Existing `market_a2a/catalog_constraints.py:28` can treat a negated feature as a positive requirement; broader natural-language matching remains unproven. All six original C1 cases narrowed to one candidate, so their pass does not prove multi-candidate model discrimination.
- Existing `market_a2a/shopping.py` checkout exceptions still propagate through the task group. Recovery of ambiguous remotely committed purchases is not implemented. The current change correctly avoids labeling every transport exception as an unpaid retry.
- Model planning remains variable: stress attempts1/2 and ordinary attempt5 failed, while stress3 and ordinary6 succeeded. Stress3 also ignored the instruction to reserve the entire remaining budget for the final item (cap150, remaining664); the actual receipt was valid nonetheless. Do not claim all instructions were obeyed or that the prompt edit alone caused success.

These are disclosed boundaries and pre-existing or empirically observed limitations, not unresolved blockers to the current fixed assignment scenario.

## LOW

The latest official GitHub README, lecture HTML source, and trade-test blobs matched local files during the earlier source check. Direct wpti deployment access was unavailable; source equivalence is established, deployment freshness is not. Failed summaries use default empty fields; actual partial purchases/plans must be read from raw logs. Full task correlations include failed tasks (6 per latest run), whereas successful receipt task IDs are5 per run.

## Independently inspected evidence

- `logs/challenge-2/replanning-attempt-3/`: actual first-shop-price-offset scenario; pass=true,20cards,2plans,5 successful purchases,1686/2200,1 failed checkout,6 ledger entries,final reserved0.
- `logs/challenge-2/attempt-6/`: no injected offset; pass=true,20cards,2plans,5 successful purchases,1757/2200,1 failed checkout,6 ledger entries,final reserved0.
- All27 source SHA-256 entries in each invocation exactly match `git show 8bb9fc7:<path>`.
- Raw `[A2A RESPONSE]` records independently yield5 unique completed task IDs,5 unique receipt SKUs, and receipt totals1686/1757. Each run's29 logged HTTP observations has version1.0.
- Raw shopping states carry four completed purchases plus one failed observation into round2; only the remaining item is replanned. No completed item is purchased again.
- Stress3: lamp shop00/cap150 fails; shop05/cap150 purchases at150. Baseline6: storage shop04/cap200 fails; remaining budget846 becomes shop14/cap846 and purchases at403.
- Prior stress source manifests also matched commits68b5fee/640ad7f. Failed outputs remain intact.

## Skill perspective and test relevance

Loaded remove-ai-slops and programming plus Python reference, and reviewed the full production/test changes. No deletion-only tests, prompt prose assertions, implementation-constant mirror tests, new untyped escape hatches, or unrelated production parsing/normalization were introduced. CheckoutAttempt directly supports the requested failure feedback. The new regression drives real shop logic with external-boundary fakes and observes remaining budget, failed-item-only replanning, no duplicate completed checkout, and released reservations. The four cap-boundary cases distinguish lower/equal rejection from higher-cap/other-shop acceptance. Older evaluator tests are useful but do not themselves exercise shop execution.

Independent test runs: official check_week06 passed; targeted tests19 passed in0.17s; after cap policy change23 passed in0.18s, with API key unset and bytecode/cache disabled. Root VALIDATION records full89tests and clean Ruff/format/strict types; these are root-run evidence, not an independently rerun full suite. No paid API execution performed by this reviewer. Prose-only final planning clarification appropriately has actual scenario evidence rather than brittle prompt-text tests.

## Approval scope

Local assignment requirements have supporting execution evidence: basic21/21, historical C1 six cases, and latest general C2 success. Additional actual failure-to-replanning-to-success is observed in two fixed runs. Preserve all earlier failures and report the limits above. PR creation and the deadline are not verified as completed; **local approval is not submission completion**. Obsidian updates are root-owned and outside this review's write scope. No source fixes were made by the reviewer.

최종 문서 대조 완료: REPORT 머리·말미와 VALIDATION의 Observable verdict가 기본21/21, C1고정6/6, 최신 일반C2 1757/2200, 추가실패복구1686/2200, 과거실패보존, 고정사례범위 및 PR미생성을 일관되게 명시한다. 현재 결과와 모순되는 완료/보류 상태는 해당 최종 요약에서 발견하지 않았다.
