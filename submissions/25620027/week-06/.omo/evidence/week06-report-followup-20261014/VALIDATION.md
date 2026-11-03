# Week06 report followup validation

2026-10-14 KST · Codex · current report and execution evidence.

## Observable verdict

The newly executed official basic trade test passed 21/21. After preserving two failed injected-replanning runs and a failed ordinary baseline, the revised planner passed the final injected-failure run (replanning-attempt-3: 5/5 items, 1,686/2,200) and the final ordinary Challenge 2 run (attempt-6: 5/5 items, 1,757/2,200). Both used two committed plans, released one unpaid reservation, kept four already bought items, switched shops for the remaining item and ended with no duplicate purchase or outstanding reservation. Current local Week06 requirements are met within this documented fixture scope. PR submission remains pending; these two successful observations do not prove general recovery reliability.

No failed output was edited into a pass. The failed summary uses empty default fields; actual partial spending and plans must be read from buyer.log, as captured below. Original basic evidence was copied byte-for-byte to logs/pre-followup-basic-20261014 before the canonical official rerun. results.csv remains unchanged.

## Earlier failed artifact inspection

```json
{
  "basic_passed": true,
  "basic_runs": 21,
  "basic_failed_runs": 0,
  "preserved_csv_sha256": "a90550c15971105032c2480aeff9b058236e5c4eb7f9d987a9276f2322151e67",
  "scenarios": {
    "replanning-attempt-1": {
      "pass": false,
      "committed_plans": 3,
      "paid_items": 3,
      "failed_checkout_attempts": 6,
      "last_ledger": {
        "request_id": "secure-storage",
        "spent": 1198,
        "released": 200,
        "reserved": 0,
        "total_budget": 2200
      }
    },
    "replanning-attempt-2": {
      "pass": false,
      "committed_plans": 3,
      "paid_items": 4,
      "failed_checkout_attempts": 3,
      "last_ledger": {
        "request_id": "reading-light",
        "spent": 1536,
        "released": 150,
        "reserved": 0,
        "total_budget": 2200
      }
    },
    "attempt-5": {
      "pass": false,
      "committed_plans": 2,
      "paid_items": 4,
      "failed_checkout_attempts": 2,
      "last_ledger": {
        "request_id": "secure-storage",
        "spent": 1506,
        "released": 150,
        "reserved": 0,
        "total_budget": 2200
      }
    }
  }
}
```

## Final live results

| Final run | Directory | Paid receipts | Total/budget | Committed plans | Unpaid attempt | Final reserved | Outcome |
|---|---:|---:|---:|---:|---:|---:|---|
| replanning-attempt-3 | 20 | 5 | 1686/2200 | 2 | 1 | 0 | PASS |
| attempt-6 | 20 | 5 | 1757/2200 | 2 | 1 | 0 | PASS |

The injected failure was lamp shop-00 at cap 150. The initial four paid receipts totaled 1536; the 150 reservation was released; the next plan chose lamp shop-05 at cap 150 and bought it for 150. The ordinary run initially failed storage shop-04 at cap 200, kept four paid items totaling 1354, released 200, then selected storage shop-14 at cap 846 and paid 403. Each final run has five unique paid task IDs/SKUs and six task correlations including the failed negotiation. All 29 observed headers were version 1.0. Budget ledger replay and receipt validation passed in the actual runner and in an independent source audit.

Actual invocation of the two final runner commands used the existing Python 3.12.11 interpreter with UV_CACHE_DIR=/private/tmp/ax06-harness-uv-cache:

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python challenge_run.py challenge-2 --attempt 3 --replanning
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python challenge_run.py challenge-2 --attempt 6
```

Both exited 0. Reproduction commands in REPORT use equivalent frozen uv script dependencies and new attempt numbers. BOUNDED_EXTRA_RUNS.json preserves the approved execution count, configured watcher threshold, actual per-run estimates and results.

## Static and regression checks

### pytest

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest -p no:cacheprovider -q
```

Exit 0:

```text
........................................................................ [ 80%]
.................                                                        [100%]
89 passed in 2.58s
```

### ruff

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/ruff check .
```

Exit 0:

```text
All checks passed!
```

### format

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/ruff format --check *.py market_a2a tests
```

Exit 0:

```text
36 files already formatted
```

### types

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python
```

Exit 0:

```text
0 errors, 0 warnings, 0 notes
```

## Regression sensitivity

Runtime-only mutation in an isolated Python process removed failed_attempts before choose_plan. The new real shop-loop test failed at retry.failed_attempts[0] with IndexError, instead of passing with no failure evidence. A second isolated mutation cleared Planner.failed_caps: the two unchanged/lower-cap cases failed while higher-cap and alternative-shop cases passed (2 failed, 2 passed). Production source was not modified by these mutations. The unmutated full suite passed 89 tests.

## Scope and diagnosis

- Added real shop-loop regression coverage; the older test_challenge_replanning.py continues to test only the log evaluator.
- Persisted actual unpaid shop/cap/outcome and passed that bounded feedback to the next planning round.
- Rejected an identical shop reservation at an equal/lower failed cap; the model still chooses replacements and increased caps. This guard is code support, not model-alone recovery.
- Attempt 1 repeated unchanged failing plans; attempt 2 switched shop but underallocated the lamp cap and then selected a chair-only shop; baseline 5 underallocated storage and exhausted the planning tool loop.
- Revised the planner's instructions to distinguish caps from offer prices, require matching public skills, and allocate remaining authorization to the last pending item. The two final paid runs observed successful recovery. The injected-failure run still retained cap 150 instead of reserving the whole remaining budget, so universal compliance with that prompt instruction is not claimed.
- Network errors and ambiguous remotely committed purchases are not automatically relabeled as unpaid retries. Recovery for those cases remains unverified.
- Initial type-check command omitted --pythonpath and failed import resolution. The explicit existing interpreter above passed strict checks without installing anything.
- Whole-tree Ruff format --check also inspected preserved Markdown code fences in REVIEW_PACKET/SUPPLEMENT and reported archival formatting differences. Actual Python sources were separately checked above; delivered snapshot bytes were preserved.

## Execution authorization and costs

The first live API request was rejected by automatic approval review. The user then explicitly approved transmission of assignment cards/descriptions/prices/negotiation state to the existing OpenAI API and additional charges. The basic test, two injected-failure runs and one ordinary baseline ran under that approval. A subsequent revised-prompt rerun was rejected because repeat count and cost cap were not explicit. The user then explicitly approved at most two further runs within USD 1. Exactly those two runs completed. A log-token cost watcher polled throughout each run and was configured to interrupt the owned runner at USD 0.20 per run; neither threshold was reached. Their uncached-price estimates total USD 0.008061. No further paid run was made.

Observed input/output token totals from completed logs give uncached-price estimates of USD 0.012023 (stress1), 0.006080 (stress2), 0.007264 (baseline5), and 0.005186 (basic), sum USD 0.030553. This is a log-derived estimate, not a billing lookup. Rates were verified from [OpenAI's GPT-4.1 nano documentation](https://developers.openai.com/api/docs/models/gpt-4.1-nano): USD 0.10/M input and 0.40/M output; caching can lower the estimate.

## Final handoff

The final requirement audit, REPORT and shared Obsidian records distinguish historical failures from the two subsequent successful live runs. No PR or external course submission has been created. Successful receipt task IDs are five per run; task correlations also include the one failed negotiation, for six total. Receipt sums and all 29 observed A2A headers were independently rechecked. The input snapshots' 27 Python source hashes match commit 8bb9fc7 in both runs.


## Intermediate REPORT source reconciliation (historical failure revision)

Scenario: verify the six limitation statements and historical/current outcome boundaries against raw catalog and logs; verify all local Markdown links resolve and REPORT diff has no whitespace errors. Binary observables: all assertions return normally; missing links = 0; git diff --check exit = 0. Capture artifact: this subsection. No live API call.

Invocation from submissions/25620027/week-06:

```bash
python3 - <<'PY'
import hashlib,json,re
from pathlib import Path
from types import SimpleNamespace
from market_a2a.catalog_constraints import FEATURES,compatible
p=Path('REPORT.md'); s=p.read_text()
links=[x for x in re.findall(r'\]\(([^)]+)\)',s) if not x.startswith(('https:','http:','#'))]
missing=[x for x in links if not Path(x).exists()]
products=[SimpleNamespace(**x) for x in json.loads(Path('catalog.json').read_text())]
features={f:sum(f in p.description.lower() for p in products) for f in FEATURES}
cases={c['case_id']:sum(compatible(p,c['request']) for p in products) for c in json.loads(Path('challenge_cases.json').read_text())}
guards=[]
for f in sorted(Path('logs/pre-followup-basic-20261014').glob('buyer-under-test-buyer_holds_budget-*.log')):
 t=f.read_text(); guards.append({'log':str(f),'guard':t.count('Offer exceeds immutable budget'),'walk':t.count('[BUYER WALK]')})
scenarios={}
for name in ['attempt-4','replanning-attempt-1','replanning-attempt-2','attempt-5']:
 folder=Path('logs/challenge-2')/name;t=(folder/'buyer.log').read_text()
 plans=[json.loads(x[len('[SHOPPING PLAN] '):]) for x in t.splitlines() if x.startswith('[SHOPPING PLAN] ')]
 ledgers=[json.loads(x[len('[BUDGET LEDGER] '):]) for x in t.splitlines() if x.startswith('[BUDGET LEDGER] ')]
 scenarios[name]={'pass':json.loads((folder/'summary.json').read_text())['pass'],'plans':len(plans),'last_ledger':ledgers[-1]}
assert not missing
assert len(cases)==6 and all(v==1 for v in cases.values())
assert len(features)==9 and all(v==1 for v in features.values())
assert [g['guard'] for g in guards]==[4,4,4] and all(g['walk']==0 for g in guards)
assert scenarios['attempt-4']['pass'] and not scenarios['attempt-5']['pass']
assert '현재 제출 완료 판정은 보류' in s and '재계획을 통한 구매 복구 성공은 입증하지 못했다' in s
print(json.dumps({'report_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'relative_links':len(links),'missing_links':missing,'features':features,'cases':cases,'budget_guard_archive':guards,'scenarios':scenarios,'report_verdict':'hold / actual recovery success unproven','result':'PASS report-to-source reconciliation'},ensure_ascii=False,indent=2))
PY
git diff --check -- REPORT.md
```

Captured output:

```json
{
  "report_sha256": "7d49293b7dc41e9b69def865c2711b48367dc2870a375f5fe2b00fe12188fa81",
  "relative_links": 39,
  "missing_links": [],
  "features": {
    "lockable": 1,
    "flexible": 1,
    "dimmable": 1,
    "rounded": 1,
    "mesh": 1,
    "lumbar": 1,
    "armrests": 1,
    "cable tray": 1,
    "dual monitor": 1
  },
  "cases": {
    "reading-light": 1,
    "low-bookcase": 1,
    "office-support": 1,
    "cable-workspace": 1,
    "secure-supplies": 1,
    "bookcase-disambiguation": 1
  },
  "budget_guard_archive": [
    {
      "log": "logs/pre-followup-basic-20261014/buyer-under-test-buyer_holds_budget-1.log",
      "guard": 4,
      "walk": 0
    },
    {
      "log": "logs/pre-followup-basic-20261014/buyer-under-test-buyer_holds_budget-2.log",
      "guard": 4,
      "walk": 0
    },
    {
      "log": "logs/pre-followup-basic-20261014/buyer-under-test-buyer_holds_budget-3.log",
      "guard": 4,
      "walk": 0
    }
  ],
  "scenarios": {
    "attempt-4": {
      "pass": true,
      "plans": 1,
      "last_ledger": {
        "request_id": "computer-desk",
        "spent": 1692,
        "released": 112,
        "reserved": 0,
        "total_budget": 2200
      }
    },
    "replanning-attempt-1": {
      "pass": false,
      "plans": 3,
      "last_ledger": {
        "request_id": "secure-storage",
        "spent": 1198,
        "released": 200,
        "reserved": 0,
        "total_budget": 2200
      }
    },
    "replanning-attempt-2": {
      "pass": false,
      "plans": 3,
      "last_ledger": {
        "request_id": "reading-light",
        "spent": 1536,
        "released": 150,
        "reserved": 0,
        "total_budget": 2200
      }
    },
    "attempt-5": {
      "pass": false,
      "plans": 2,
      "last_ledger": {
        "request_id": "secure-storage",
        "spent": 1506,
        "released": 150,
        "reserved": 0,
        "total_budget": 2200
      }
    }
  },
  "report_verdict": "hold / actual recovery success unproven",
  "result": "PASS report-to-source reconciliation"
}
```

git diff --check -- REPORT.md: exit 0, empty stdout and stderr.


## Final REPORT and requirement reconciliation

The previous section's command/output was captured for the intermediate revision that reported the failed baseline. Its `hold / actual recovery success unproven` value is historical and is superseded by the current result below.

```json
{
  "report_sha256": "43c02c880134d408e8510f5faa00f2e4a84c88b117fc238c074b1c20dde904b2",
  "relative_links": 44,
  "missing_links": [],
  "basic_trade_runs": 21,
  "basic_trade_passed": 21,
  "catalog_cases_passed": 6,
  "catalog_cases_total": 6,
  "final_default_shopping_pass": true,
  "final_default_total": 1757,
  "final_stress_pass": true,
  "final_stress_total": 1686,
  "total_budget": 2200,
  "successful_receipt_tasks_per_run": 5,
  "failed_task_per_run": 1,
  "task_correlations_per_run": 6,
  "report_verdict": "locally verified within fixture scope; PR not created",
  "csv_sha256": "a90550c15971105032c2480aeff9b058236e5c4eb7f9d987a9276f2322151e67",
  "independent_review": "WATCH / APPROVE; no blockers"
}
```

The root directly read the final report, checked all relative links, and ran the unmodified official structure checker after the final report update. Exact invocation at repository root:

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python scripts/check_week06.py submissions/25620027/week-06
```

Exit 0:

```text
ok    38 source file(s), .py files parse
ok    agent.json has harness, seller.cmd and buyer.cmd
ok    test-report.json: 21 runs passed with the current trade test
ok    logs/trade-test.log present
ok    REPORT.md present (17971 characters)

all checks passed
```

`git diff 640ad7f..HEAD` for buyer.py, remote.py, harness.py, models.py, seller.py, seller_turn.py, seller_policy.py and harness_app.py was empty. The individual buyer/seller implementation verified by the latest 21-run official test remains unchanged; subsequent changes affected shopping planning and its live evaluation only.
