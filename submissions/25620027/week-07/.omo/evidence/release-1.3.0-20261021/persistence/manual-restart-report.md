# Public restart helper readiness

Implemented only `tests/manual_public_restart.py`. The network phases were deliberately not executed by this executor; the parent owns the public canary and Docker restart. No public result/review submission exists in this helper.

All checks used `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/` from the week-07 project root:

| Scenario | Invocation | Binary observable | Artifact |
|---|---|---|---|
| Strict lint | `ruff check tests/manual_public_restart.py` | All checks passed | `manual-restart-checks.log` |
| Format | `ruff format --check tests/manual_public_restart.py` | 1 file already formatted | `manual-restart-checks.log` |
| Types | `basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python tests/manual_public_restart.py` | 0 errors, 0 warnings, 0 notes | `manual-restart-checks.log` |
| Programming skill audit | `python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.40/skills/programming/scripts/python/check-no-excuse-rules.py tests/manual_public_restart.py` | no violations in 1 file | `manual-restart-checks.log` |
| Safe CLI discovery | `python -m tests.manual_public_restart --help` | Exit 0; required open/finish phase shown; no network phase entered | `manual-restart-help.log` |

Operator sequence: `python -m tests.manual_public_restart open`, recreate the container retaining its state database, then `python -m tests.manual_public_restart finish`. Each phase checks card version 1.3.0 and exclusively creates its own trace. The open task is exclusively saved in `public-restart-task.json` in the release folder. Finish sends numeric 100 with taskId only, verifies preserved task/context and registered-key signature, checks terminal replay returns error without result, and verifies one distinct first-message explicit-offer sale. Its exclusive result is `public-restart-finish.json`. Maximum expected successful receipts: two.

Public replay rejection proves no additional published receipt; absence of a backend SALE event requires the parent's server-log check. Numeric 100 may legitimately receive a counteroffer if the initial ask exceeds 105; this helper preserves the requested completed-sale assertion and does not weaken it. Actual public outcomes remain unverified until the parent runs both phases.

Initial lint failures are preserved in `manual-restart-ruff-first.log`; initial type results in `manual-restart-types-first.log`. No skill modifications were needed.
