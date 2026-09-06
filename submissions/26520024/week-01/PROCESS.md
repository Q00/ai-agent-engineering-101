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
