# Compound negative minimum-grade review / revision06

- AGENTS/status and previous REPORT/control verified before edits. Start ad35d38.
- Preserve existing dirty logs/report, untracked originals and all prior failed logs.
- Reproduce initial/later with/without price; consume supported minimum suffix,
  reject unsupported compound negatives globally only when a grade is specified.
- Review bounded clause grammar and harmless product-name substrings.
- Only local changes / remote exclusive candidate validation. No production
  replacement, restart, signal, state/key access, RPC/POST, install or push.
- Resume verified HEAD 5024fd7 and the existing dirty seller patch; no rollback.
- baseline-focused 119 pass; red-focused 124 / 38 subtest-inclusive failures.
- Interrupted final-focused completed: 124 / one fixture failure. `B급` has always
  selected the cheap cohort, so bare `B급 이상` legitimately selects B6, not A72.
  Correct the new fixture rather than change existing selection/price behavior.
- Interrupted final-all has no BACKPORT_TEST_RESULT and stops during an unrelated
  restock test. Preserve it as incomplete, not a test pass or assertion failure.
- Fixture corrected without selection changes. resumed-focused: 124 pass;
  resumed-all: 551 discovered, 549 pass, 2 unchanged launcher skips.
- Global compound-negation check, consumed supported suffix, 128-character explicit
  condition span cap and harmless-name boundary regressions all pass.
- Source attribution 2fbd842. Revision06 prepare/dependencies/stage/validate/export
  completed: VM focused 124 pass, full 492 discovered / 490 pass / 2 launcher skips.
- Fresh final read-only observation and verify.py final succeeded: production HEAD,
  clean status, 56 source hashes and process start metadata unchanged. No activation.
