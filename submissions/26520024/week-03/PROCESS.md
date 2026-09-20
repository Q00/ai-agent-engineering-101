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
