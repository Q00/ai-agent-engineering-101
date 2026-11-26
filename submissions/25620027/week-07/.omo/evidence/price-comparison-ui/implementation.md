# Price comparison UI candidate handoff

## Scope and design

Implemented only `.market/seller-ad.comparison.next.html`, `assets/price-comparison.js`, and `tests/test_price_page.py`. The existing live `assets/seller-ad.html` was read, not replaced. Backend, monitoring, runtime, logs, credentials, and root reports are owned by other workers. No deployment, browser session, external API, process action, or commit was performed.

The OMO frontend skill and design/perfection references were read. The explicit task contract to preserve the existing appearance and edit only the candidate takes precedence over introducing a new DESIGN.md/design system. Reused the existing color tokens, neutral borders, rounded panels, typography, and card rhythm. The comparison table owns horizontal scrolling on narrow screens; the page remains document-scrolling. No dependency, font, animation, or decorative accent stripe added. React tooling is inapplicable (vanilla static HTML/JS).

## Behavior

- Always six body rows: our shop and five peer slots. Missing peer slots and individual observations stay visible. Columns: shop, board rank, lamp, chair, bookshelf, per-item check time in KST.
- Own prices use ≤ and are labeled new-negotiation opening ceilings, with baseline caps when comparison is unavailable. Peer prices are observed first asks, not settlement prices or equivalent-quality promises.
- Lower/equal/higher is computed against the minimum of currently valid observed asks and displayed only when the backend count/minimum agrees. Text states N of 5 peer shops; no whole-market price claim.
- Backend fresh/enabled flags, price kind, both snapshot dates, 15-minute maximum age, and per-quote timestamps are checked. Expiry has its own timeout; no need to wait for the next poll to remove claims. Stale prices turn muted and explicitly say expired/unverified. Failed refresh suppresses comparisons, and recovery restores them.
- Same-origin GET `/price-comparison.json` every60seconds, no-store, 10second abort bound; visibility restoration refreshes the page data. No peer requests. Server external refresh600seconds is disclosed separately.
- External names are assigned via textContent. Links permit only HTTPS without credentials; invalid URLs are plain text. Remote error messages never appear verbatim. Static catalog, coupon and buyer instructions remain usable with JS absent or failed.

## Executed evidence

From the week-07 submission using existing cached Python/Ruff/basedpyright:

|Scenario / invocation|Binary observable|Artifact|
|---|---|---|
|`python -m pytest -q tests/test_price_page.py -p no:cacheprovider`|11passed; shipped JS executed in Node VM with controlled DOM/network/clock|behavior-tests-final-pass.txt|
|Fresh six rows, scoped lower/equal/higher, incomplete slots, unknown/disabled, expired data, timer expiry without fetch, failed fetch/recovery, hostile name/URL, fixed same-origin polling, invalid price kind|All10 executable JS scenarios passed|behavior-tests-final-pass.txt; scenario source tests/test_price_page.py|
|HTMLParser structure: static buyer navigation, six scoped column headers, caption, noscript, deferred script, machine-readable nonfixed prices|Passed as11th test|behavior-tests-final-pass.txt|
|`node --check assets/price-comparison.js`|exit0|js-syntax-final.txt|
|`ruff check tests/test_price_page.py`|All checks passed|ruff-final-pass.txt|
|`ruff format --check tests/test_price_page.py`|1file already formatted|format-final-pass.txt|
|`basedpyright --pythonpath <cached-python> tests/test_price_page.py`|0errors,0warnings,0notes|types-final-pass.txt|
|`git diff --exit-code -- assets/seller-ad.html`|exit0 at handoff capture|live-ad-check.txt|

Initial Python lint findings and embedded JS fixture formatting failures are preserved in earlier result files. The fixture was repaired and the complete relevant11-case suite rerun. No production check was weakened. The HTML copy received one final nonbehavioral wording clarification after that suite run.

## Parent-owned remaining QA

This is a tested candidate, not a deployed or visually approved page. Parent owns mobile/desktop browser rendering, live GET/route checks, and promotion of the candidate to assets/seller-ad.html. No Lighthouse scores or screenshot fidelity are claimed. The skill's Bun accent-stripe checker could not run because Bun is unavailable on PATH and the standard local locations; the new CSS uses only existing neutral table rules and a focus-visible ring. No new reusable skill rule was established, so no skill was modified.
