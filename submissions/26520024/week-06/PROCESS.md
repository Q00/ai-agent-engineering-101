# Process

- Started branch week-06-26520024 from main 3defe07. User requested the required
  assignment only and no push. Read the README, SDK-based lab seller, official
  trade-test implementation and structural checker.
- Base is Python 3.8.19; a2a is absent. Confirmed from package metadata that
  a2a-sdk 1.2.0 needs Python >=3.10. Asked about a project-local isolated runtime
  instead of changing base or any existing conda environment.
- Plan: reuse the prior model adapter, SDK server, guarded model decisions,
  FTS5 catalog retrieval, and model-selected multi-shop budget allocations.
  Implementation and analysis are assisted by Codex; all live model calls will
  be recorded separately from scripted offline tests.
# Implementation and runtime update

- User selected the existing `eraseanything` interpreter after the base Python
  incompatibility was explained. It is Python 3.10.22, but has no A2A SDK.
  A project-local `.venv` uses that interpreter; no packages in any existing
  conda environment were installed, upgraded or removed.
- Added the seller/buyer, 3,016-item synthetic catalog, six frozen descriptive
  cases, twenty shop configurations, and three four-item shopping runs.
- The first SDK lifecycle test exposed SQLite's default thread affinity under
  Starlette TestClient. The failure was reproduced in `logs/offline-01-failed.log`
  before changing the connection to allow cross-thread access with a lock.
  All 15 offline tests then passed (`logs/offline-02.log`). These tests use
  explicitly scripted decisions, not live model evidence.
- Before live execution, corrected `agent.json` to the course's nested
  seller/buyer command schema. No official test had yet been run.

## Genuine live runs

- Official unmodified trade test: one invocation, 21/21 passed, 36 model calls.
  Its original test-report.json and all official logs remain at the required paths.
  No official rerun, hand-edited result, hidden failed trial or mock model was used.
- Challenge 1: 20261020T052534380628Z, six precommitted descriptions, six correct
  item receipts and no reserve violation, 12 real seller model calls. The fixed
  counterparty accepts the ask and is explicitly labeled a scripted probe.
- Challenge 2: 20261020T052658723825Z, twenty actual local seller servers and real
  fetched cards, three model plans and twelve concurrent per-item negotiations.
  All three shopping lists completed; totals 765, 755, 760 against budget 900.
  Model calls: three planner, twelve buyer, twenty-four seller. All servers were
  stopped by the runner's finally blocks after preserving their logs.
- Independent verification passed: all 87 real decisions agree with raw CLI
  responses, constraints hold, six official buyers exit normally, and all four
  challenge-2 purchases overlap in time in each shopping run. No native tool
  actions appear in the raw model events.
- Wrote a Korean report including the actual offline failure, genuine results,
  synthetic-catalog limitations and the fact that challenge-2 negotiations were
  short. Did not claim long-context robustness, optimality or a measured speedup.
- No extra-credit tasks, remote deployment, push or PR were performed.
- The direct structural check entered a NAS filesystem wait while recursively
  enumerating the submission, including ignored runtime/cache folders. Observed
  process state `D`, wait channel `rpc_wait_bit_killable`; stopped that checker
  and the concurrent stuck git-status command, not the experiments or servers.
  Added a byte-identical export of source/data/logs excluding generated runtime
  caches, then invoked the unchanged official checker on that clean export.
  This is only a structural-check workaround; the official live trade test ran
  on the original submission directory without changes to the course script.
- Clean-export structural check passed with all eleven source files parsing and
  all twenty-one genuine trade results matching the unchanged official kit.
  `logs/structural-check-03.log` records the final export and checker output.
