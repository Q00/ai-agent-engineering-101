# Goods selection deployment

Approved scope: fast-forward 5c5298a to 822ca8f (two commits: prior deployment evidence and goods-selection changes). No commit/push performed by deployment helper.

## Safety / rollback plan

- Verify clean affected paths, Git-blob SHA256 against LF remote files, existing guarded wrapper and 60-second watchdog before FF.
- Back up only seller.py and seller_cash_policy.json. No production state, inventory, credentials, environment, crontab, or original launcher read/copied.
- Before seller signal, any failure leaves old seller runtime running. If restoration becomes necessary, copy the two verified backup files individually over their original paths; never reset/rewrite Git history. Report the resulting intentionally dirty files. Do not blindly retry or signal after a failure.
- Exactly one SIGTERM to verified seller PID, with identity/starttime/parentage checks. Existing watchdog restarts seller. Kit, buyer, tunnel, pane and VM are not restarted. In-flight in-memory negotiations may be lost.
- Tests run from a unique isolated tracked-code archive, excluding live untracked state/keys; socket audit blocks external connections. Synthetic test output retained verbatim in new log files.
- Only board and local/existing registered public card GETs. No registration, negotiation, checkout, posting, or test trades.

## Completed results

- Final verification: **2026-11-05 23:36:44 KST** (`2026-11-05T14:36:44.782659+00:00`). Remote HEAD: `822ca8fb48c02841b16604039b50cfacfdddd666`. Runtime seller/config paths clean.
- FF bundle contains exactly two commits after `5c5298a9ce2f3d420e2fd166acbbd3848a026452`; endpoint verified through named `HEAD`. Bundle SHA256: `3ab55b767a8a74647d60579b7369bd66557c26f6c9a1d063f6d8e423023c4df7`.
- Remote seller SHA256 matches target Git blob: `74c5e246806694e35eb222097cf005695bce5e6ecdf6bda71039f3469580d67d`.
- Remote seller_cash_policy.json SHA256 matches target Git blob: `6c648feefc6b9846f3daed6af82645b2619e9475dd3cfc2a1523e11598d11c7f`.
- `/home/comalswjd/venv/bin/python -B deploy.py validate`: **56 focused tests passed** (goods-selection 29 + warranty 12 + grade 15); **368 full offline discovery tests passed**, zero failures/errors. Full discovery uses `test_*_offline.py` in an isolated tracked-code tree. Local reported 396 includes 28 methods in the untracked `test_market_monitor_offline.py`, intentionally not deployed.
- `focused-offline.log` and `all-offline.log` contain verbatim synthetic test stdout/stderr, not production market logs. Validation took 0.276s and 4.781s respectively. No external test-network connections permitted.
- Exactly one seller SIGTERM. Old seller PID/starttime/parent: `62923` / `1506835` / `62921`; replacement: `112294` / `2810737` / `112292`. Both independently verified by `/proc` executable, cwd, parent ancestry to unchanged kit, and port `52507`. Watchdog performed the replacement; no additional signals.
- Kit remains PID `43679`, starttime `1037901`, parent `43669`. Tunnel remains PID `43696`, starttime `1038186`, parent `43679`. Buyer remains in the unchanged kit; no buyer restart.
- Guarded kit, guarded wrapper, agent configuration and underlying kit source hashes unchanged. The existing 60-second watchdog was verified before deployment.
- Local agent-card GET **200**; existing registered public agent-card GET **200** at unchanged `https://combines-spelling-sam-parish.trycloudflare.com`; `/api/board` GET **200**. Final role-specific matches: `/shops/20` mjforge **alive=true**, `/buyers/21` mjforge **alive=true**.
- `remote-before.json`, `remote-applied.json`, `remote-tests.json`, `signal.json`, `remote-after.json` and `final-check.json` record the evidence; `evidence-export.tar` brought these reports, synthetic logs and runtime-only backups to the local directory. Local backup SHA256 re-verification and helper syntax compilation passed. Limited text-artifact credential-pattern scan returned no matches (not a universal secret-detection guarantee).
- Local pre-existing modified logs/test-report and untracked files preserved. No commit/push, dependency installation, state/inventory changes, new infrastructure, registration POST, verification trade, payment or procurement was performed.

## Preserved failed attempts

1. Initial preflight stopped because prior deployment evidence existed untracked. Hash-by-hash verification confirmed seven existing incoming artifacts were identical; runtime remained untouched.
2. First FF still refused those identical untracked files. No HEAD/runtime change and no signal occurred. Only those seven verified artifacts were adopted into Git's index, then FF succeeded with original artifact content/paths intact. The runtime-only backup from the first attempt was reused after hash verification, without overwrite. `index-adoption.json` records the exact adopted hashes. Attempt `.log` files are concise failure notes, not raw console transcripts.

## Remaining limits

No live negotiation/payment verification was performed intentionally. In-flight in-memory negotiation preservation cannot be guaranteed across seller termination. No rollback was needed.

## Coordinator verification

At `2026-11-05T14:38:47.864Z` (23:38:47 KST), independent GET checks confirmed
board HTTP 200, both mjforge seller and buyer `alive=true`, and the registered
public agent-card HTTP 200. Deployment evidence is preserved in a separate local
commit; this does not change the deployed VM HEAD `822ca8f`. No Git push performed.
