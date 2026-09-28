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
