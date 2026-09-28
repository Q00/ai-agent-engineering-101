# Process and provenance

## Before execution

- Read the local week-04 README, lecture implementation example and course checker.
  The working branch is week-04-26520024, based on local course commit 51c09f4.
  No fetch/pull/push/PR was performed; the student explicitly postponed them.
- Preserve existing staged week-02/ARCHITECTURE.md and all prior submissions.
- Reuse the week-03 Codex adapter's isolation approach, with public conversation
  history instead of a single bidding input. Existing ChatGPT login, not an API
  key copied from an account. Confirmed CLI 0.153.0 and Python 3.8.19 in base.
- Scenarios describe fictional GPU-slot purchases, not real infrastructure jobs.
  Two feasible cases (including equal limits) and two infeasible cases are frozen.
- Use the lecture's eight-message limit and full-transcript observer. Only actor
  format instructions/protocol reading change. No forced opening question.
- Keep format errors observable: no response schema or JSON repair. Do not guard
  against an out-of-limit deal using private evaluator values.
- Mark unknown CLI sampling parameters honestly. Implementation and report are
  assisted by Codex; actor/reader responses will be preserved unmodified.

## Pre-run implementation review

- Removed a literal example price of 80 from format/reader instructions before
  any model call, to avoid anchoring a condition to a particular scenario price.
  The original prompt remains in the first design commit; scenarios are unchanged.
- Added strict parsing, a public-only state machine and evaluation after closure.
  A no-offer acceptance is a protocol error, not a fabricated transaction.
- The runner logs every attempt, preserves crash rows and resumes by (run, scenario)
  without repeating recorded episodes. Completed log rows can restore a missing
  CSV append after interruption. A process lock prevents concurrent writers.
- 32 offline tests passed in existing base before any live call. They cover
  private-limit separation, identical non-format prompts, tagged reader behavior,
  malformed JSON, no-offer acceptance, out-of-limit deals, boundary cases, turn-limit
  outcomes, retry bounds and append-only crash/recovery behavior.

## During execution

- Started the first actual call on 2026-09-28 after commit b787481, without a pilot
  or excluding warm-up episodes. Code, prompts and scenarios remain frozen.
- Added offline raw-event replay during the live batch. Seven new tests check
  complete replay and detect CSV, raw response, private input, flags, frozen
  prompt and usage tampering. All 39 offline tests passed. Synthetic evidence is
  generated only in temporary directories, never actual logs or results.csv.
- The first free run produced valid deals in S1/S2 and unresolved negotiations
  at the turn limit in S3/S4. Those open outcomes remain failures; no prompt or
  scoring change was made in response to them.
- The archived FIPA standard links were unavailable to the documentation tool.
  Used the original SC00061G PDF mirrored by PUC-PR and explicitly identified the
  accessible CAL copy as XC00037H (2001 experimental), not the 2002 final version.

## Actual results and review

- Completed 36 episodes in the planned nine runs on 2026-09-28. No transport
  crashes, retries, replacement episodes, prompt tuning or repaired outputs.
  Original CLI stderr warnings are retained alongside the raw successful turns.
- Each condition produced six valid deals, one explicit no-deal and five open
  outcomes. All 15 open failures remain, with correct=0. Violations and format
  errors are zero. No forced opening question or invented reader error was added
  to reproduce the lecture's anticipated failure pattern.
- Free used 85 actor/85 reader calls; tagged 87/79; structured 90/0. The 426
  completed model calls used 3,785,732 input and 8,154 output tokens according to
  the original events. Input includes CLI overhead and cached tokens once; this
  is not a dollar-cost estimate. Episode wall times sum to 2,098.805 seconds.
- Tagged's refusal in repeat 02 was not a stable advantage: free also refused
  correctly in repeat 03, and structured did so in S3 of repeat 03. Final
  accuracy ties at 7/12. Tagged repeat 03 instead chose reject-proposal at its
  final turn, illustrating why an explicit act does not itself ensure closure.
- Replayed all 36 episodes offline from original model events. Exact actor/reader
  inputs, flags, tool-free event types, parsed acts, states, private-limit scoring,
  usage and CSV fields matched. The report includes the full numerical table and
  line-linked examples, distinguishing parse errors from semantic correctness.
- Preserved design, implementation, tests and progressive run evidence in local
  commits. Existing staged week-02/ARCHITECTURE.md remains outside these commits.
  No conda environment was created or modified; no packages were installed.
  No fetch, pull, push or PR has been performed.

## Final local verification

- Course check passed: six Python files, four scenarios, 12 episodes per
  condition, nine run logs and the report. The 39 offline tests and all 36
  raw-evidence replays passed; original command output is under verification/.
- Confirmed every report episode row and aggregate against results.csv, and
  every quoted format/reader prompt against prompts.json. Checked local links.
- Reran --repetitions 3 after completion: no new model calls, with identical
  SHA-256 hashes for all nine logs and results.csv before/after the command.
- Original stderr contains 1,278 state-db fallback warnings (three per call).
  They are retained unchanged; each of the 426 calls still has successful raw
  completion/usage evidence. No Codex state database or configuration was edited.
- Credential-marker scan and ownership check passed. The unchanged course
  ownership script uses a Python 3.10-style type annotation; executed it in base
  Python 3.8 with postponed annotations, without editing the shared script.
- Whitespace check passed. All our commits touch only week-04; the unrelated
  staged week-02/ARCHITECTURE.md is still staged and excluded from our commits.
