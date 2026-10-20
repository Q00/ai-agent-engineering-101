# LAB 1: personal evidence before public reputation

The user delegated the strategy choice. The starting hypothesis is that remembered reliable shops and short negotiations will improve completion and repeat visits. This does not promise first place. No fake reviews, extra identities or invented receipts are used.

## How the buyer chooses

`market_buyer.py` reads the provided shop list and selects exactly one shop. `negotiation/market.py` scores candidates with explicit initial weights: 50% smoothed personal completion evidence, 20% affordability from observed prices for the requested item, 20% smoothed public stars, and 10% observed speed. These weights are a testable policy choice, not an empirically optimized result.

Memory is keyed by shop **name** and item, so a new tunnel URL does not erase experience. The buyer reads at most the latest 500 trade records, uses up to 30 records per shop, and passes only five matching personal records plus ten bounded, deduplicated public reviews to the negotiation harness. Duplicate review text is counted once. Recent personally observed receipt faults receive a strong 30-minute penalty. Multiple poor public reviews cause a smaller caution penalty, not a permanent ban.

During review, a weakness was found in fixed weights: malicious low ratings could still outweigh several personally successful trades. The corrected policy reduces the public weight to `0.20 / (1 + personal observations)` and transfers the difference to personal evidence; the public caution penalty shrinks in the same way. A dedicated regression test ensures repeated successful personal trades survive a public smear. No claim is made that this defeats every fake review.

After six observations, every seventh order can explore an untried shop for this item, excluding candidates with multiple poor reviews or recent personal integrity failures. If there is no such candidate, the buyer uses the highest evidence score. This cadence is an initial experiment and should be adjusted from real outcomes.

A low-budget failed negotiation is not classified as dishonesty. Own success, network failure and receipt mismatch remain different outcomes. Public reviews are untrusted data and cannot change the budget or authorize actions.

## Reuse and boundaries

Negotiation reuses the week-06 `negotiate` loop and `ModelHarness` with gpt-4o-mini. Model-selected offers still pass `parse_buyer_decision`, which rejects offers above the budget. `BudgetClient` adds a second check immediately before sending a numeric follow-up. No hidden budget is inferred from someone else's public review.

The receipt validator checks the requested item, agreed price, budget and any provided task id, then returns the original receipt data Part without rewriting its numeric representation or signature fields. Failed validation yields no receipt in the market result. The final output line has the selected listing URL, the original receipt or null, stars and a factual review.

Reviews use the observed result: a matching affordable receipt earns 4 stars, or 5 for a short purchase at no more than 90% of budget; an ordinary no-deal earns 3; transport/harness failure earns 2; receipt/protocol-integrity failures earn 1. These are stated policy choices, not an assertion that every low-rated shop was dishonest.

Memory is append-only JSONL in `memory/market-trades.jsonl`: timestamp, shop name, item, budget, result, actual price, elapsed seconds, price-message count and task id. Selection reasons and evidence are logged in fresh `logs/market-buyer/run-*/buyer-events.jsonl` files. Corrupt memory fails visibly instead of silently deleting past failures.

## Seller strategy

The seller prompt favors a modest initial margin, meaningful counteroffers and terminating repeated unaffordable offers. Its reserve validation is unchanged. Prices and negotiation actions remain model decisions. Lower opening prices and shorter exchanges are hypotheses to compare against the preserved baseline, not measured improvements yet.

## Validation and deployment

The first implementation passed 93 local unit/regression tests, including changed URLs retaining reputation, personal receipt faults outweighing public praise, review spam deduplication, budget blocking before transport, and preserving original signed receipt fields. Output is preserved in `logs/setup/lab1-unit-*.log`.

The official market test will be run before restarting the market kit. LAB 1 expects all buyer/protocol/review checks and the existing seller checks to pass. The three seller signature checks belong to LAB 2 and are expected to fail until seller signing is implemented; they must not be hidden or reported as passes.

The current market may still be Round 0. In that case the kit uses the original `buyer` command until the market moves to Round 1. A Round 0 join is not evidence of a live Round 1 review being published.

### Official test result

`python scripts/market_test_week07.py submissions/26510121/week-07` completed with **22/25** passing. The only three failures are `seller_signed_receipt`, each reporting `receipt lacks shop, task`. Thus LAB 1's buyer, budget, A2A and review-choice requirements passed; the overall market test still returns exit code 1 and is not an assignment-wide pass.

In all three purchase cases the buyer sent a single price offer of 100, purchased within budget 120, and returned the original receipt. All three unaffordable bookshelf cases sent no offer over budget 230 and ended with a null receipt and a factual review. Good-review selection was 3/3 and sent no requests to the bad-review shop. The seller preserved its reserve, but repeated below-reserve counters still occurred in the official test; the prompt change has not demonstrated shorter seller negotiations yet.

Raw console output, isolated synthetic memory, and untouched copies of the previous canonical test files are in `logs/market-tests/20261020-201714/`. `test-report.json` and `logs/market-test.log` now describe this market-test run. Test memory uses `MARKET_BUYER_MEMORY` to stay separate from live `memory/market-trades.jsonl`.

After the initial 93-test regression run, all 9 market-specific tests passed with the extra public-smear case. The broader suite was not unnecessarily repeated for this ranking-only correction.

### Restart and current participation

Restarted the kit with the updated `agent.json`, same nickname and same signing keys. Public-key fingerprints were compared before and after restart and matched. Cloudflare quick-tunnel attempts at 20:21 and 20:23 failed to provide a URL; a diagnostic attempt at 20:24 remained at requesting a quick tunnel and was stopped. Each session's output and errors were preserved. The launcher now saves info-level cloudflared diagnostics, restoring the parent's environment afterward.

Following the explicit tunnel-failure fallback in https://market.ourolabs.ai/agent.md, the kit was restarted with `--buyer-only`. At **20:25:35 KST on 2026-10-20**, it logged `buyer registered as seochanit, round 0` and received another market order. The buyer is participating; the shop is currently unreachable because its public tunnel is unavailable. The source still includes the seller and the tested market buyer. This is not a claim that full shop deployment or R1 live-review verification is complete.

At registration the market was still R0. The updated kit has read the `market_buyer` command and will use it when the market issues Round 1 orders. Real R1 `shops.json`, selection logs and accepted reviews remain an external-round checkpoint; local official tests verified their buyer-side behavior. To resume in buyer-only mode, use `agent-market/join.ps1 -BuyerOnly` after setting `MARKET_OWNER` locally. To retry full shop participation later, stop the current kit first and launch without `-BuyerOnly`.

### R1 live checkpoint, 2026-10-20 20:32 KST

The market subsequently changed to R1. A fresh API read confirmed `round=1` and `current=1`. Buyer participation was restarted independently at 20:32:18 and the actual kit invoked the new `market_buyer` command.

- The kit generated real `.market/shops.json` containing 36 shops, excluding seochanit.
- Order `o-9af691cf` selected corner-store with the printed evidence score. No deal at budget 205; the market accepted the factual 3-star review in ledger #4855.
- Order `o-b9c85381` selected corner-store, bought a bookshelf for 275 within budget 295 after two price messages, and returned the original receipt. Both signatures verified; ledger #4877 recorded `verified True` and `review accepted` with 4 stars.
- Another chair order at budget 160 selected grand-furniture and returned no receipt, with its review accepted in ledger #4914.
- Live memory is now appended to `memory/market-trades.jsonl`; the synthetic test memory remains separate.

Thus the buyer-side LAB 1 live checkpoints were observed, rather than inferred from test fixtures. The shop remained OFFLINE. An SSH alternative was attempted only after port 8900 was verified to serve Seochanit Shop's official kit proxy, but the connection to localhost.run port 22 timed out. A separate Cloudflare diagnostic tunnel was rejected by automatic approval review as a duplicate exposure outside the latest R1 status-check request. No rejected tunnel was started. Explicit user direction was requested before another public shop connection attempt.

The launch helpers now support separate roles: `join.ps1 -BuyerOnly` records `.market/session.json`, while `join.ps1 -ShopOnly` records `.market/shop-session.json`. A pre-existing public URL can be supplied using `-PublicUrl <URL> -Port <dedicated proxy port>`. `stop.ps1` stops the normal/buyer session; `stop.ps1 -ShopOnly` stops a separate shop session. Claim codes and API keys remain environment-only.

### Shop outage diagnosis, 2026-10-20 20:36 KST

The user explicitly approved restoring the shop as well. A shop-only attempt was kept separate from the working R1 buyer. The course kit's original tunnel loop discards cloudflared's failure output and can busy-wait until its deadline after the child exits, hiding the provisioning error.

Added `agent-market/run-kit.py`, which imports the unmodified official kit and replaces only its tunnel startup with bounded diagnostics. It verifies that the actual proxy port serves Seochanit Shop, captures all info-level cloudflared output to a fresh log, and stops immediately if the child exits. The wrapper's syntax and the official kit CLI help were checked. Application source and market protocol remain unchanged.

The raw output in `logs/market-sessions/tunnel-raw-20261020-203612-*.log` identified the actual error: **`quick tunnel provisioning failed with status 429`**. This is Cloudflare's provisioning rejection, not a nickname, claim-code or seller-card error. Further automatic quick-tunnel retries were stopped; no attempt was made to evade the rate limit. No reset time was provided in the captured output. SSH to localhost.run port 22 had separately timed out. The independent R1 buyer remains running, while the shop is still offline pending an available public tunnel or a deployed shop URL.
