# Review evidence improvement — 2026-10-21

(Codex) The user requested an improvement after observing repeated cross-shop promotion in public reviews. Inspection found that existing code already excluded raw review text from the buyer's selection input. This change addresses duplicate signal amplification and the missing matching-receipt claim. It is a local buyer improvement, not a seller deployment or automatic-buyer activation.

## Behavior

- At most the latest10 supplied reviews per shop are projected. Each same-round boolean signal pattern appears once, preserving first appearance order. Different adverse/positive patterns survive. Unknown authors and stable reviewIDs are not invented; this intentionally loses frequency information, including potentially independent corroborating claims.
- Raw review text, promotional shop names, URLs embedded in reviews and public rating numbers stay outside the selection evidence. Original registry shop identities/URLs and locally observed own incidents retain their existing handling.
- A narrow English recognizer adds `reviewer_reports_matched_receipt` for wording such as “receipt matched” or “receipt matches.” The model is told this is an unverified positive claim, not a verified receipt; absent flags mean unknown. This is not a multilingual semantic classifier, broad claim verifier or proof of injection immunity.
- Third-party review patterns alone do not block a seller, post a warning or assign a punitive public rating. Existing local incident, budget, SKU and receipt boundaries remain covered by regression tests.

## Verification and attempts

- Before implementation,new regression scenarios produced8 failures/1 pass: repeated4/10 entries remained separate and the positive receipt signal was absent. [Initial regression](regression-before.log).
- Related buyer/security tests passed61/61. [Targeted run](targeted-tests.log).
- First full offline run passed230/230. Added a conflicting positive/overcharge pattern scenario; final **231/231** passed, including10 new cases. [Final full run](offline-tests-final.log).
- Ruff passed all changed files. Required public API contract docstrings were added after the initial D101/D103 findings; an extra blank line then producedD202 and was removed. These original outputs remain. [Final lint](ruff-final-pass.log).
- First type-check invocation resolved the wrong environment and could not locatepytest. With the cached Python interpreter explicitly selected, final changed-file check reports0 errors/0 warnings. No diagnostics were suppressed. [Final types](types-final.log).
- Instructor structural checker passed. Its25-run report line checks the existing earlier report; **the paid25-case market test was not rerun on this candidate**. [Structural check](structure.log).
- Replayed saved14:52 own/dragonash public review windows through the real projection:10 reviews become2 distinct signal patterns for each; the promotional name is absent and receipt agreement claims remain. No model API was called. [Replay](real-review-replay-final.json). The first replay also attemptedcheapest-in-town, which exists onboard but was absent fromshops reviews, raisingStopIteration before output; its empty output file is retained and the final replay explicitly covers only the two present shops.

## Operating scope

No deployment,restart,rejoin,new purchase,review/report posting,limit/deadline change or credential read occurred for this change. Auto-buyer remainsOFF. Current live seller remains1.3.1-limit10000;15:13 read showshealthy,4885/10000,baseline4354,deadline21:00 KST. The separate scheduled observation is saved in../market-monitor-20261021/snapshot-1512. Neither this offline verification nor the observed5 additional market deals establishes a causal trading improvement.
