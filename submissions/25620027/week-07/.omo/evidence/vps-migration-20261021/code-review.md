# VPS migration independent code review

Status: WATCH. Recommendation: APPROVE. Final local code review after watermark correction; blockers: none.

Scope: all newly added deploy/vps/{Dockerfile,Dockerfile.dockerignore,compose.yml,guard.py,serve_public.py,test_guard.py,test_public.py}. Files are untracked additions, so their full current contents are the reviewed diff. Existing seller/harness inspected only to establish runtime semantics. No source/test edits, remote actions, or model calls performed.

Goal: preserve seller 1.2.0 identity, signing key and public URL; no buyer/rejoin; fixed 2026-10-21 09:00 KST deadline; cumulative observed MODEL_USAGE threshold 5000 across migration/restart; private read-only credential mount, tmpfs runtime key, UID10001 and resource caps; staging without tunnel; isolated Hermes deployment.

## CRITICAL
None.

## HIGH
None remain. Resolved H1: guard.py:87-92 now rejects reconstructed usage below persisted watermark before preparation or child launch. guard.py:124-134 fsyncs log before atomically persisting every observed usage increment. The original reproduction now raises ValueError, leaves the 5000 watermark unchanged and creates no seller.log. Missing/truncated log regressions are behavioral tests, not deletion-only tests: they exercise fail-closed budget enforcement.

## MEDIUM
M1 — Default guard test evidence root is not repeatable or concurrent-run safe. deploy/vps/test_guard.py:18,23-24 uses a stable test-name directory with exist_ok=False and no cleanup. A second ordinary invocation fails in setup. Use a per-run directory or documented explicit unique VPS_TEST_EVIDENCE; this review reran with mktemp. This is test-maintenance friction, not a deployment correctness blocker.

## LOW
None.

## Evidence inspected and independently checked
- deploy/vps/.omo/evidence/guard-tests.txt: original 8 passing cases.
- deploy/vps/.omo/evidence/public-routes.json and public-card.json: recorded four 200 routes, version 1.2.0, existing advertised URL. These are offline ASGI observations, not proof of real VPS networking/signature identity.
- Independent run: VPS_TEST_EVIDENCE=$(mktemp -d /tmp/vps-review-tests.XXXXXX) python3 -B deploy/vps/test_guard.py — 9 tests passed, 0.888 seconds, including newly added staging tunnel disable case.
- Independent temporary-directory H1 reproduction: baseline=4998, stored usage=5000, no seller.log -> child executed, returned 1, persisted usage=4998. Reproducer only launched a local Python print child.
- Offline burst check: baseline4999 + five MODEL_USAGE lines -> persisted5004/model_usage_limit. Parent clarified the accepted contract is stop at observed threshold, not an API-attempt hard cap; therefore this is an explicit limitation, not a blocker.
- Docker allowlist excludes private key/env/log files. Compose uses RO private bind, separate data/market binds, tmpfs runtime key, UID10001 image, CPU/memory/pid limits and bounded restart. No buyer/rejoin commands found. Stage tunnel disable is present. Actual stage/prod bind separation, credential ownership, remote image execution, cutover freeze/baseline finalization, signature match and Hermes isolation require deployment evidence; local source does not establish these.

## Skill perspective
Loaded and applied omo:remove-ai-slops and omo:programming before maintainability/test judgment. No deletion-only tests, prompt prose tests, or unnecessary production extraction/normalization found. Boundary JSON validation and output parsing are required for this guard. Meaningful subprocess behavior tests cover limits/restart/deadline/signals; M1 violates isolation/repeatability perspective. One test includes a fixed deadline constant assertion alongside actual expired-deadline behavior; this minor redundant assertion does not substitute for the behavior test. Production files stay below 250 pure LOC. Skill retrospective: no reusable skill change warranted; managed skill files untouched.

No ulw-loop plan: omo-agent-toolkit ulw-loop status --json returned ULW_LOOP_PLAN_MISSING. Report uses parent-assigned fallback artifact location. No notepad path or implementation report supplied at this interim point.

## Final independent verification
The corrected guard and updated tests were reread. All 11 guard scenarios independently rerun using a fresh temporary evidence directory; output below. Original H1 reproduction independently repeated and rejected before launch. No remaining CRITICAL/HIGH findings. M1 remains a nonblocking test ergonomics issue. Actual image build, deployed public health/signature, finalized baseline and stage/prod separation remain deployment validation, not established by this code approval.

```text
test_absolute_deadline_prevents_child_launch (__main__.GuardTests.test_absolute_deadline_prevents_child_launch) ... ok
test_cap_and_restart_keeps_cumulative_baseline (__main__.GuardTests.test_cap_and_restart_keeps_cumulative_baseline) ... ok
test_child_failure_returns_nonzero_for_bounded_restart (__main__.GuardTests.test_child_failure_returns_nonzero_for_bounded_restart) ... ok
test_deadline_stops_silent_children (__main__.GuardTests.test_deadline_stops_silent_children) ... ok
test_deleted_log_cannot_reduce_persistent_count (__main__.GuardTests.test_deleted_log_cannot_reduce_persistent_count) ... ok
test_existing_persistent_usage_counts_on_restart (__main__.GuardTests.test_existing_persistent_usage_counts_on_restart) ... ok
test_lost_baseline_fails_closed (__main__.GuardTests.test_lost_baseline_fails_closed) ... ok
test_replaced_baseline_fails_closed (__main__.GuardTests.test_replaced_baseline_fails_closed) ... ok
test_sigterm_stops_both_children_normally (__main__.GuardTests.test_sigterm_stops_both_children_normally) ... ok
test_staging_disables_only_tunnel (__main__.GuardTests.test_staging_disables_only_tunnel) ... ok
test_truncated_log_cannot_reduce_persistent_count (__main__.GuardTests.test_truncated_log_cannot_reduce_persistent_count) ... ok

----------------------------------------------------------------------
Ran 11 tests in 0.896s

OK
```
