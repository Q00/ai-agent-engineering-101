# Process and provenance

## Before execution

- Read weeks/week-03/README.md, week-03.html and AGENTS.md.
- The student chose diffusion safety, replacing hate_safety with ip_safety.
  The agreed experiment routes review requests, rather than running detectors.
- The student requested implementation and execution, conda base only, no push
  until they understand the results. Existing environments are not modified.
- Confirmed Python 3.8.19 in base and Codex CLI 0.153.0 with ChatGPT login.
  No API key values were accessed. No API key environment variables were set.
- Reused the isolation/tool-action validation approach from week-02-ver2's
  Codex adapter, but do not constrain output with a bid JSON schema: malformed
  JSON must remain a measurable failure. Embedded roles are a CLI limitation.
- The lecture explicitly permits unavailable temperature controls when marked
  unknown. No fabricated temperature=0 or determinism claim is made.
- Checked the lecture's actual manager code: only positive bids count toward
  course messages. Declines still consume calls and are logged separately.
- Existing staged week-02/ARCHITECTURE.md is unrelated and must stay untouched.
  A new local week-03-26520024 branch preserves it; commits are path-scoped.
- tasks.json, prompts.json and DESIGN.md are frozen before live calls. All code
  and documentation are assisted by Codex; model-produced bids are saved raw.

## Implementation and offline verification

- Implemented strict JSON validation, stable first-response tie breaking, and
  gold-blind allocation separated from evaluation. Positive-bid-only message
  accounting follows the course example exactly.
- Added append-only CSV/log handling and crash preservation. Fixed an undefined
  validator config-key reference during initial code inspection, before runs.
- 23 offline unit tests passed in base Python 3.8.19 before live execution.
  They cover malformed JSON, duplicate keys, nonfinite scores, declines, ties,
  gold isolation, prompt invariance, native-action rejection and crash retention.
- No prompts, gold labels or response schemas were tuned against pilot runs;
  the first model call will be part of the submitted experiment.

## First live repetition

- Executed from existing base using the committed design/code (410561c).
  Every run records that revision and SHA-256 hashes of its actual inputs.
- Runs 001/002/003 completed: correct = 6/2/6; messages = 30/42/34;
  misawards = 0/4/0. No unassigned tasks, parse failures or crashes.
- C did bid outside its specialty under the overconfident instruction, usually
  at 95, but the matching specialists bid 100. This is a null effect on awards,
  not proof of robustness. Do not tune the prompt to force a failure.
- Codex emitted state-database fallback warnings to stderr but returned
  successful completed turns. Warnings are preserved verbatim in raw logs.
- During execution, added four isolated validator tests (27 total), checking
  valid replay and detection of altered CSV, frozen inputs, and final replies.
  Only tests/docs changed; experimental code, tasks and prompts stayed frozen.

## Completed experiment

- All nine planned runs completed, with 162 actual model calls, on the original
  frozen tasks/prompts/code. No pilot trials, discarded attempts, extra model
  calls for rewriting bids, or post-result tuning were performed.
- The second and third repetitions reproduced the first: baseline 6/6 correct
  and 30 messages, homogeneous 2/6 and 42, overconfident 6/6 and 34.
- Across three repeats: baseline 18/18 correct; homogeneous 6/18 with 12
  misawards; overconfident 18/18 correct. All unassigned/parse-failure counts
  are zero. There were no actual crashed runs to omit or replace.
- Every homogeneous positive bid was 100, so all awards went to A by the
  preregistered tie rule. Overconfident C bid 95 on 12 out-of-specialty tasks
  and 99 on its six IP tasks; the other matching specialists bid 100.
- Raw totals: 1,426,479 input tokens, 6,061 output tokens, 60 declines, and
  1,107.752 seconds of aggregate run time. CLI input overhead is included;
  this is not a monetary-cost comparison or a minimal direct-API token count.
- The raw-event replay verified every input prompt, model response, usage
  event, parsed bid, stable-tie winner, gold evaluation and CSV count for all
  nine runs. Output: verification/live-validation-01.log.
- Wrote the four-part report with original Smith-paper comparison and exact
  run-log line references. The report explicitly distinguishes routing from
  actual image safety detection and reports the null award effect honestly.
- Changes remain local on week-03-26520024. No push or PR is performed; the
  student will review and understand the results before deciding to submit.

## Final checks

- 27 offline tests passed; the course structural check passed with six Python
  files, six balanced tasks, three rows per condition, nine logs and the report.
- The ownership checker passed in base using postponed annotation evaluation
  (`__future__.annotations` compiler flag); the shared checker itself was not
  changed despite its Python 3.10-style annotations.
- Credential-pattern scanning found no common PAT/API-key/private-key patterns.
- Default `git diff --check` flagged the CSV writer's standard CRLF endings as
  whitespace. Kept the original output unchanged; a one-command `cr-at-eol`
  whitespace setting passed. No Git configuration or experimental code changed.
- Existing staged week-02/ARCHITECTURE.md is excluded from all week-03 commits.
