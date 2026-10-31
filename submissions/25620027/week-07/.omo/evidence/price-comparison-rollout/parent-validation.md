# Observed opening-price comparison rollout — 2026-10-21

## Candidate validation

- Initial full-suite checkpoint: 126 passed, then 148 passed after routes/UI/monitor tests. Final offline suite after two observed-format parser additions: **167 passed in 2.39s**, `full-suite-post-parser.log`.
- Official kit with `AX_PRICE_COMPARISON=0`: **25/25 PASS**, `official-attempt-1.log`. This covers seller/buyer/signature/review contracts without external competitor probes. The later parser-only addition is independently offline-tested and does not run in this official configuration; the official paid suite was not repeated.
- Structural `check_week07.py`: all checks passed, `structural-attempt-1.log`.
- Changed source/tests: Ruff ALL and strict basedpyright 0 errors/warnings, `ruff-final.log`, `types-final.log`.
- Reviewer initially blocked mismatched product metadata and duplicate/self selection. Both were corrected, and two follow-up APPROVE records remain in `../price-comparison-code-review.md`. Advertised prose is never passed to our decision model; the bounded extra parser extracts only the numeric quote in a full known format. It does not claim to identify every instruction embedded in the ignored prose.
- Root manual helper now verifies card1.2.0, public enabled/fresh comparison, one lamp negotiation, and receipt signature with the market-registered key. It is explicit opt-in execution, not pytest collection. Its live result is recorded separately after deployment.

## Initial external observations

`initial-snapshot.json`: 2026-10-21 00:52:11 KST, up to 15 opening messages and best-effort CancelTask. LSS100/180/260; Corner130/234/338; Dragon102/185/268. 11im/Jang were initially unavailable because their text formats were unsupported. Two additional lamp openings/cancellations confirmed exact bounded formats (`../price-comparison-monitor/diagnostic-quotes.json`); no offers, purchases, market-result or review submissions. Raw text is evidence only. A2A openings may consume the other shop's model calls or affect visit counters; this is not read-only GET telemetry.

## Deployment contract

Keep the existing tunnel and same registration25620027/key/address. Enable only the seller via AX_PRICE_COMPARISON=1. Refresh600s; expire900s; at most60 monitor refreshes per seller lifetime. The existing supervisor still ends at **2026-10-21 09:00 KST or cumulative MODEL_USAGE5000**, whichever occurs first. No automatic buyer. Basic product ceiling=max(reserve,min(ceil(reserve*1.10),lowest fresh observed ask-1)). Known prices imply100/180/260, tied with LSS because the reserve is equal. Unknown/expired/unsupported/variant prices do not support a cheapest claim. Observed first asks are not completed prices or evidence of identical quality. In-flight negotiations do not increase their asking price when the comparison expires.

## Development attempts

The candidate JSON-terms updater first looked for agent-terms; the actual element is shop-terms. The first operation exited before any write and the corrected target updated version1.2.0 plus comparison metadata. Sandbox ps was denied; the approved process-only read confirmed the exact old kit33933 and guard33934. No credential values were read or copied.

## Activated deployment and signed public check

Source commits3d7b1d1 (collector),46e668e (seller),efc0925 (page). Old kit33933 stopped normally at cumulative251 orders/2663 model calls; its supervisor33025 exited0. The existing fixed tunnel41396 was kept. New supervisor47498 started kit43272 and caffeinate43273; the same name25620027 registered in R2 at **2026-10-21 01:00:59 KST** with the same key/address. Price comparison is enabled. Deployment startup and the public check are in `deployment-through-010119.log`.

At01:01:19, the public1.2.0 lamp negotiation opened at100, countered an infeasible1 at100, then accepted100; receipt price/item/task matched and the registered public key verified the Ed25519 signature. `public-smoke.json`, `public-smoke-1.log`, `public-smoke-stdout.log`. No market result or review was submitted. Public browser rendering showed our ceilings100/180/260 tied with the lowest of3 measured peer shops and preserved2 unavailable rows. This does not measure improved traffic, sales, ranking or reviews.

`git diff --cached --check` flagged trailing whitespace in preserved prior failing pytest logs under price-comparison-ui. Those raw logs were deliberately not rewritten; source whitespace checks remain separate. The old public HTML is preserved as previous-ad.html; its historical1.1.0 terms are not the deployed current terms.

## First actual periodic refresh and page update

The enabled seller reused the fresh00:52:11 seed, then automatically refreshed at **01:02:11 KST** (expires01:17:11). The01:02:43 public JSON and browser page, without reloading, showed **5/5** shop observations for all three basic products:11im110/195/290; lee-sungsoo100/180/260; corner-store130/234/338; dragonash102/185/268; JangSeongWoong115/195/275. Own ceilings100/180/260 are tied with the lowest observed ask for each product, with reserve protection. This validates the first scheduled collection and browser60s data update, not an entire overnight observation history. Evidence: `public-page.json`, `first-background-refresh.json`, `browser-rendered.json`, `runtime-through-first-refresh.log`.

Desktop browser rendering was visually inspected: numeric values, dates, disclosed scope and comparison relationships were readable. The narrow viewport scroll behavior is covered by UI behavior/structure checks; a manual mobile viewport test was not performed. Existing public key/name/tunnel and seller-only09:00/5000 limits remain unchanged.

The activated tracked HTML plus routes were rechecked after replacing the candidate:12/12 passed (`activated-ui-routes.log`). Source/assets/tests whitespace checks passed. At01:05:10 KST, kit43272 and caffeinate43273 were still running; cumulative market-log MODEL_USAGE2667 of5000 and frozen buyer-order lines251. This is a process observation, not a claim that the eventual09:00 stop has already occurred. Current supervisor command aliases retain AX_PRICE_COMPARISON=1 for same-session follow-up operations.
