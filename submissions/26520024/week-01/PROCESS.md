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
