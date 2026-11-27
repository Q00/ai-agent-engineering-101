# Goods floor deployment

## Approved procedure and rollback plan (before action)

- Deploy only committed `822ca8f..b215011` (two commits); production change is seller.py only. Preserve all unrelated local/remote modifications and untracked files.
- Verify affected paths clean and LF file SHA256 against Git blobs. Only byte-identical incoming prior evidence may be adopted into the index; differing files stop deployment. No delete, reset, overwrite, or history rewrite.
- Back up seller.py only and record before/after hashes. Verify policy, guarded wrapper, agent card configuration and kit source unchanged, including existing 60-second watchdog.
- Fast-forward only. If FF fails, do not touch runtime or send a signal. If offline tests fail, do not signal: report the cause and, only if required, restore the individually verified seller.py backup after checking current bytes equal the deployed target. No automatic destructive rollback or repeated restart.
- Isolated tracked-code archive tests, Python -B, no credentials/state/inventory copying and no external test sockets. Preserve raw synthetic test logs in unique files. No installs.
- After passing tests and /proc identity checks, send exactly one SIGTERM to seller only; wait less than three minutes for the existing watchdog. Preserve kit/buyer/tunnel/VM/boot configuration. In-flight in-memory negotiations may be lost.
- Public verification uses GET only: local card, existing registered card, API board seller/buyer alive. No transactions, registration, procurement, settlement or posts. No infrastructure created.
- No commit or push by this helper. Existing logs remain untouched.

## Results

**STOPPED in preflight; target not deployed.** Weekly assignment README is absent at weeks/week-08/README.md; deployment follows the explicit approved specification.

- `python -B .../prepare.py` passed: verified two-commit ancestry, production path seller.py only, compiled helper syntax and built named-HEAD bundle. Bundle SHA256 `049f21d97279341c136658db8a461172f4fe5515da60a7e6a43629ffa70d8007`.
- gcloud SSH verified original remote HEAD `822ca8fb48c02841b16604039b50cfacfdddd666`, no tracked or staged changes. Single deployment tar transferred using gcloud scp, extracted only into new unique evidence directory.
- `/home/comalswjd/venv/bin/python -B deploy.py inspect` exited 1 at `prior_evidence_differs_.../20261105-goods-selection-deploy/DEPLOYMENT.md`. Read-only follow-up hashed all incoming existing evidence: 20 files exist untracked; 18 match committed blobs and two differ. No index adoption was performed.
- Differing prior `DEPLOYMENT.md`: actual `a8e9d8890ad452e03a338dd4b443737ae98bef1d6ecf27ac8c9dfccf0cfb0218`; expected `4279f5509a33562c3e042a622f850d40feecf8480b7a0e140be8ffe10886ae9d`.
- Differing prior `manifest.json`: actual `d943d41ad8d73ec35e3c04dafab755feca16d1c87b36949a7da34c0f3077c0c1`; expected `9b41ec983776c6ef8590a2b2827f6a3a8142375b4db548e8459e8ebe408506d6`.
- Explicit mismatch-stop rule honored: no fetch/FF, production edits, backup/restore, index adoption or signal. No old evidence overwritten or deleted. Remote tracked/index changes remain empty. Code backup is not created because inspect stopped before the mutation/backup phase.
- Seller SHA256 remains base `74c5e246806694e35eb222097cf005695bce5e6ecdf6bda71039f3469580d67d`. Policy SHA256 remains `6c648feefc6b9846f3daed6af82645b2619e9475dd3cfc2a1523e11598d11c7f`. No production configuration changed.
- Final GET verification at `2026-11-05T15:27:58.026304+00:00` (2026-11-06 00:27:58 KST): local card 200, existing registered card 200, API board 200, `/shops/20` mjforge alive=true and `/buyers/21` mjforge alive=true. No market quantities collected.
- Seller PID 112294 / parent 112292 / starttime 2810737 / port 52507, cwd `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08`, executable `/usr/bin/python3.10`. Kit PID 43679 / starttime 1037901 and tunnel PID 43696 / starttime 1038186 remain unchanged from prior deployment. Kit ancestry verified by /proc. No buyer process restarted; board buyer alive=true.
- Remote focused/full tests **not run** because preflight failed. Expected 72/384 are not actual results and must not be reported as passing. No raw test output exists for this attempt.
- `preflight-stopped.json` and `final-check.json` brought back locally via gcloud scp (one remote source per command). Evidence tar contains only the stopped-preflight JSON, not production data. Existing local user changes preserved. No commit/push.

## Required next decision

The explicit approval permits adopting only exact-match evidence, and forbids overwriting/deleting differing evidence. Coordinator must obtain a safe, separately approved resolution of these two prior-record collisions before attempting deployment again. Do not silently relax checks or deploy seller bytes outside the approved FF path. Runtime is still the prior goods-selection version; goods-floor protection is **not deployed**.
