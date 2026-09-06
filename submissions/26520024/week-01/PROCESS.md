# Development record

## 1. Starting point

- The student asked the coding assistant to implement the week-01 assignment.
  The code and documentation in this branch are assistant-assisted work, not
  evidence that the student independently wrote or ran them.
- Started from the course's Anthropic two-tool example and its input file.
  Kept its model, tool schemas, tool dispatch dictionary, and feedback loop.
- Created a separate week-01-26520024 branch from the student's roster commit.
  Roster PR #38 was still open when work began.
- Neither ANTHROPIC_API_KEY nor OPENAI_API_KEY was set. No live model call has
  been performed. A console capture from a real run is still required.
- Python 3.12.10 and a temporary virtual environment are used for local checks.
  The system's default Python is too old for the course check scripts.

- The first implementation patch could not be applied because the patch tool's
  filesystem sandbox failed to start. The implementation had not changed at
  that point; retrying through the approved shell's apply_patch executable.

## 2. Third tool and task

- Added write_note to both TOOLS and TOOLS_IMPL. It appends UTF-8 text under
  outputs/; it cannot overwrite the input, code, or logs through its path API.
- Chose a small GPU-time bookkeeping task. The input is explicitly synthetic.
  Its expected sum, calculated independently, is 1.25 + 2.50 + 0.75 = 4.5 hours.
- Replaced the starter memo because its date, attendee count, and reimbursement
  made "sum the numbers" ambiguous. This was a design decision, not a failed
  model run.
- Added --tools 2 and --tools 3 to offer different tool sets with the same goal
  and loop. Dispatch also checks the offered tool set; the baseline cannot
  execute write_note even if a response requests it. No tool order is scripted.
- Fixed the starter's string-prefix path check: resolved paths must stay under
  the submission directory, even through symlinks. Reads reject oversized
  files rather than silently returning incomplete numbers.
- Bounded arithmetic inputs and returned tool exceptions as error observations.
  Incomplete model responses and exhausted step limits now produce failures.
- Kept the starter's default model alias claude-sonnet-4-5 and made it
  configurable. Installed and pinned anthropic 1.4.0 in a temporary environment;
  inspected its actual API signature. Model availability is not yet verified.

## 3. Local verification

- All 13 offline tests passed. They exercise real arithmetic and filesystem
  tools, including append behavior, traversal and symlink rejection, oversized
  inputs, dispatch restrictions, error feedback, and maximum-step termination.
- Loop tests use explicitly scripted responses containing SDK content blocks.
  They are software tests, not empirical evidence of the model choosing tools.
- Saved the original test output in logs/offline-tests-01.log and the course
  structural check in logs/structural-check-01.log. The structural check passed.
- Python syntax and the CLI help were checked successfully.
- Live API execution was skipped because no API key was configured. There is
  no real model run, no model-generated saved report, and no observed tool-choice
  comparison. README.md and OBSERVATIONS.md identify these remaining steps.
- The course structure check accepts offline log files. Its green result must
  not be interpreted as satisfying the requirement for real agent execution.

## 4. Delivery attempt

- The ownership check passed for the changed student files; its original output
  is saved in logs/ownership-check-01.log.
- Attempted to push week-01-26520024 to the student's origin. Git could not read
  a GitHub username because this execution environment has no usable credentials
  and terminal prompts were disabled. No remote branch was created by this push.
- Implementation and verification remain in local commits. No assignment PR
  was opened. Live model logs, observations, and an authenticated push remain.

## 5. Switch to the user's authenticated Codex

- The student explicitly requested using Codex instead of Anthropic.
- Confirmed Codex CLI 0.153.0 is logged in through ChatGPT. Its configured
  model is gpt-6-astra. No credential values were read into the submission.
- Replaced the Anthropic SDK transport with codex exec and a constrained JSON
  decision schema. Python still selects the offered tool set, dispatches each
  requested tool, returns observations, and applies the maximum-step limit.
- Codex runs each decision in an empty temporary directory with a read-only
  sandbox. User configuration is not loaded; shell, external tools, plugins,
  hooks, and delegation features are disabled for this experiment. The existing
  login is reused. The output event stream is checked for internal tool actions.
- Both modes use gpt-6-astra at low reasoning effort and the same instructions.
  The model must request read_file to obtain input and calculator for arithmetic;
  Python does not prescribe their order or provide the reference answer.
- Removed the Python SDK dependency. Preserved the earlier code in its commits
  and all prior test logs. New live outcomes will be recorded separately.
