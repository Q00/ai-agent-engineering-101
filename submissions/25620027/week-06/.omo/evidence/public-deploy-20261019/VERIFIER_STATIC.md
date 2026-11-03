# Public verifier static implementation evidence

Scope: only `submissions/25620027/week-06/verify_deployment.py` was implemented. No API calls, installations, real trades, or Git mutations were performed. This document captures direct tool results; runtime public-seller behavior remains for the root executor to verify.

Working directory for every invocation below: `submissions/25620027/week-06` in the current worktree.
Interpreter: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python` (abbreviated `PYTHON` below).

| Scenario | Actual invocation | Binary observable | Captured result |
| --- | --- | --- | --- |
| Syntax compilation | `PYTHON -` with `compile(Path('verify_deployment.py').read_bytes(), 'verify_deployment.py', 'exec')` | No exception; explicit PASS | `compile: PASS` (tool chunk 385840) |
| All configured Ruff rules | `PYTHON -m ruff check verify_deployment.py` | Zero diagnostics | `All checks passed!` (tool chunk 5a31ce) |
| Strict basedpyright | `PYTHON -m basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python verify_deployment.py` | Zero errors/warnings | `0 errors, 0 warnings, 0 notes` (tool chunk 5a31ce) |
| CLI construction | `PYTHON verify_deployment.py --help` | Usage displayed without error | Required `--url <str>` and `--output <path>` options displayed (tool chunk 5a31ce) |
| HTTP URL rejection | `PYTHON verify_deployment.py --url http://example.com --output /tmp/w06-verifier-should-not-exist` | Nonzero exit before directory creation/network | Exit 1; stderr contains `URL must use HTTPS` (tool chunk 385840) |
| Private IP rejection | `PYTHON verify_deployment.py --url https://127.0.0.1 --output /tmp/w06-verifier-should-not-exist` | Nonzero exit before directory creation/network | Exit 1; stderr contains `public IP address required` (tool chunk 385840) |
| Existing output refusal | `PYTHON verify_deployment.py --url https://example.com --output .` | Nonzero exit before network | Exit 1; stderr contains `FileExistsError` (tool chunk 385840) |
| Formatter | `PYTHON -m ruff format --check verify_deployment.py` | No reformat requested | `1 file already formatted` (tool chunk 385840) |

The three guard invocations were run via `subprocess.run(..., capture_output=True, text=True, check=False)`; each assertion checked both nonzero exit and its listed stderr fragment. No server request occurs before these guards.

`awk '!/^[[:space:]]*$/ && !/^[[:space:]]*#/' verify_deployment.py | wc -l` returned 250 (tool chunk 5a31ce). This is the skill warning band; additional expansion should split responsibilities rather than exceed the limit.

Implementation review: the file owns bounded public seller verification. It reuses `Remote`, its Pydantic wire models, and `create_client`; runtime boundary parsing is not duplicated. The recorder intentionally accumulates exchange counts. No Any/casts/type ignores, async runtime substitution, unrelated refactor, or added test suite. The skill is plugin-managed and was not modified; no reusable skill defect was identified.

Pending live criteria: four cases (lamp 120, chair 200, bookshelf 300, bookshelf 230), raw request/response recording, actual version-header counts, task/context continuity, reserve checks, exact basic receipt SKU/item, and terminal-task error -32004. Static checks do not claim these passed. Root should invoke `PYTHON verify_deployment.py --url PUBLIC_HTTPS_URL --output logs/deployment/20261019/attempt-1` with a nonexistent output directory and inspect `summary.json` plus transcript/raw exchange files.

Evidence artifact: `.omo/evidence/public-deploy-20261019/VERIFIER_STATIC.md`.

## Direct captured command output — evidence repair

Command: ['/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python', '-c', "from pathlib import Path; p=Path('verify_deployment.py'); compile(p.read_bytes(), str(p), 'exec'); print('compile: PASS')"]

```text
compile: PASS
EXIT_CODE=0
```

Command: ['/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python', '-m', 'ruff', 'check', 'verify_deployment.py']

```text
All checks passed!
EXIT_CODE=0
```

Command: ['/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python', '-m', 'basedpyright', '--pythonpath', '/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python', 'verify_deployment.py']

```text
0 errors, 0 warnings, 0 notes
EXIT_CODE=0
```

Command: ['/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python', '-m', 'ruff', 'format', '--check', 'verify_deployment.py']

```text
1 file already formatted
EXIT_CODE=0
```

Command: ['/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python', 'verify_deployment.py', '--help']

```text
                                                                                
 Usage: verify_deployment.py [OPTIONS]                                          
                                                                                
 Verify a public HTTPS seller and write only to a new evidence directory.       
                                                                                
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ *  --url           <str>   [required]                                        │
│ *  --output        <path>  [required]                                        │
│    --help                  Show this message and exit.                       │
╰──────────────────────────────────────────────────────────────────────────────╯

EXIT_CODE=0
```

All captured static invocations exited zero: True. Live public trades remain outside this delegated implementation scope.
