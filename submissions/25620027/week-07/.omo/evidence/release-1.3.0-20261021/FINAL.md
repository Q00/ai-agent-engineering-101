# Release 1.3.1 verification

Completed 2026-10-21, approximately 10:11 KST. Evidence directory retains the initial 1.3.0 name and all intermediate failures.

## Observed final behavior

- Public https://ax-week07.treasuresky.xyz/ serves seller 1.3.1, Docker image sha256:0ae2979ac9aa6e61a7d47e381d922916d2e86cb9bd966da732794c6f34e410eb. `deploy-1.3.1.log` binds the actual container replacement to that version; `runtime-1.3.1-final.log` records running/healthy.
- Latest official model-backed run: `official-attempt-2/console.log`, **25/25 PASS**. Raw result files are retained under its `result/`; prior results are preserved separately. Test-only buyer memory was copied aside and the pre-test memory restored after each run.
- Full offline candidate run: `opening-settlement-offline-final.log`, **221 passed**. Independent final seller/persistence/settlement rerun: `reviewer-opening-settlement-final.log`, **42 passed**, including eight persistence scenarios. The subsequent extraction of the opening settlement helper is covered by that independent rerun.
- Scoped types: `opening-settlement-types-clean.log`, zero errors/warnings. Ruff: no errors, with the existing custom SIZE_OK marker warning retained in `opening-settlement-lint-clean.log`. Whole-tree pre-existing lint/type findings are recorded in `lint-all.log`/`types-all.log`, not claimed clean.
- New SQLite file is 0600, UID/GID10001. `runtime-1.3.1-final.log` records the immutable baseline4354, cumulative observed usage4516, limit5000, deadline2026-10-21T21:00:00+09:00. The guard stops on the first limit reached; in-progress response logging may continue during shutdown.

## Live failure and correction

1. `public-smoke.json`: initial1.3.0 lamp100, infeasible1, then exact100 receipt verified against registered public key. No market result or review posted.
2. `public-restart-task.json`/`public-restart-container.log`/`public-restart-finish.log`: a real Docker replacement preserved an open1.3.0 task. Sending taskId without contextId recovered the same context, completed exact100 with valid signature, and rejected terminal replay.
3. The same finish attempt then failed because a fresh explicit `offer lamp 100` produced a quote instead of a receipt. Its traceback and raw response remain unchanged. Initial regression tests reproduce the error. An overlong scripted fixture then exceeded the unchanged six-call production budget; that failed test run is also retained. The redundant invalid pre-search fixture action was removed, retaining wrong-SKU, wrong-price, available-item rejection and quote-retry behavior.
4. Final1.3.1 validates the retrieved item and amount, returns model feedback requiring exact acceptance of a feasible explicit opening offer, and still requires a valid model accept tool call. `public-restart-offer.json`/`.log`: one fresh `offer lamp 100` completed exact100 with a signature verified using the market's registered public key. Offline125 additionally verifies no clamping to reserve.
5. `runtime-supplement-final.log` confirms exactly one seller receipt for the resumed task and one for the final explicit offer; no receipt for the unsuccessful intermediate fresh task. Hermes container identity/start time/restarts and Week06 service health are unchanged. The first supplementary lookup failed because VPS lacks rg; the grep fallback completed, with both outputs preserved.

## Scope and limits

- Public ordinary/good completed ratings are integer3 under the user's maximum3.5 request; ordinary no-deal remains3, directly observed operator-directive incidents1. Measured facts appear in prose. Historical ratings are untouched. Selection excludes numeric public scores while retaining concrete review claims, own prices, outcomes and safety observations. Others' responses to public ratings remain uncontrolled.
- Autonomous buyer and rejoin remain OFF. No real public review or market-result submission was made by these canaries. The new review policy applies at the next authorized buyer activation.
- Coupons no longer imply reductions at reserve; selected-SKU turns avoid redundant catalog search. Seller decisions still use the existing Week05 harness and OpenAI nano model.
- Browser AX/screenshot verified the new scoped co-lowest promotion (`browser-qa.md`). External observations refresh every600seconds and expire at900seconds. The page polls its own server every60seconds. No global-cheapest, identical-quality, final-trade-price or advertising-effect claim is made; default shops.json does not expose the HTML to selection.
- The first1.2.1-to1.3 upgrade cannot migrate in-memory tasks. Persistence applies to new1.3 sessions and later process replacements. No server reboot, multi-worker, exactly-once receipt delivery or zero-downtime claim is made. A crash after terminal journaling and before delivery fails closed.
- Independent code review is WATCH/APPROVE, blockers none. The accepted model-review phase cost/reporting-failure debt remains documented. Required challenge effect analysis and the Week07 PR are outside this release and still incomplete.

Source commits: e01e33e (review policy),729e2ea (scoped promotion),0413ee5 (seller persistence/explicit offers),6130bf0 (observed opening-settlement correction). Documentation and evidence are recorded in the subsequent commit. No push/PR or market re-registration was performed in this release.
