# Week 07 Report

## 1. Harness and model

The agents use Codex CLI through `harness.py`.

On my Mac I initially used Codex CLI 0.162.1 with the default model. For the always-on deployment I moved the project to a Windows lab PC, where Codex CLI 0.163.0 was installed.

On Windows, Python subprocesses could not initially find the `codex` executable even though it worked from PowerShell. I changed `harness.py` to use `%APPDATA%\npm\codex.cmd` on Windows.

The default `gpt-6.1-sol` model later became unavailable because of a capacity/usage limit. I therefore changed the harness to explicitly use `gpt-6-luna` and verified the updated harness before restarting the market agents.

The harness uses `codex exec --skip-git-repo-check --ephemeral --color never`.

## 2. How the market buyer selects a shop

In Round 0, the market kit selected a registered shop at random and ran my original Week 06 buyer. The buyer had no market memory and no reviews.

For Round 1 I added `market_buyer.py`. The market kit writes available shops and their latest reviews to `.market/shops.json`. My market buyer reads this file and also reads `.market/buyer-memory.json`.

For each shop, the harness receives the shop name and URL, recent reviews and ratings, and my buyer's previous trade results with that shop.

The buyer is instructed to prefer shops with better reviews, avoid shops with repeated negative reviews, and treat shops without reviews as unknown rather than automatically bad. It logs why it selected a shop and then negotiates while Python enforces the order budget.

Buyer memory records recent outcomes by shop, including the shop name, item, price when a deal occurred, and whether the result was `deal` or `no deal`.

For every market order, the buyer prints a final JSON result containing the selected shop, receipt or `null`, stars, and review.

## 3. Rounds 0, 1, and 2

### Round 0

The kit selected shops randomly. My Week 06 buyer negotiated using only the item, seller responses, and budget. It had no reviews or past-trade memory.

Unsigned receipts could still appear in the ledger.

### Round 1

After adding `market_buyer.py`, the buyer started selecting shops itself using public reviews and its own trade memory.

This changed the buyer from random routing to reputation-based routing. It could avoid repeatedly poorly reviewed shops and prefer sellers with records of fair prices and matching receipts.

### Round 2

For Round 2 I added Ed25519 signatures to seller receipts. The seller signs canonical JSON containing `shop`, `item`, `price`, and `task` with `shop_key.pem`.

The market kit verifies `sig_seller`, adds the buyer signature, and submits the transaction. Only verified receipts behind valid market orders count toward reviews and market share.

The deployed seller accumulated hundreds of verified Round 2 transactions. At the Challenge 1 before snapshot, the dashboard showed 409 deals, 1% share, and 4.76 stars. At the after snapshot, it showed 907 deals, 1% share, and 4.77 stars.

The buyer also completed verified Round 2 transactions. For example, the Challenge 2 evidence contains a chair purchase from `corner-store` for 190 with `verified True` and an accepted review.

## 4. What failed and what changed

The original Week 06 seller did not sign receipts, so the market initially reported receipts without `sig_seller`.

I added Ed25519 signing using `shop_key.pem`. The private `.pem` files are ignored by Git and are not committed.

When I moved the deployment to the Windows lab PC, Python subprocesses could not find Codex. I changed `harness.py` to use the Windows npm-installed `codex.cmd`.

The default `gpt-6.1-sol` model later became unavailable because of a capacity/usage limit, so I explicitly changed the harness model to `gpt-6-luna`.

While adding the adaptive seller logic for Challenge 1, the first restart also failed because `market_context` had not been initialized inside `seller_decision`. I fixed the function, compiled the file successfully, and restarted the market kit.

Before these final Challenge 1 changes, the Week 07 fixed market test passed all 25 tests (`25/25 passed -> PASS`). I rerun the test after the final changes before submission.

## 5. Challenge 1 ??adaptive seller prices

The seller reserve prices remain fixed:

- lamp: 100
- chair: 180
- bookshelf: 260

The seller does not accept an offer merely because it is above reserve. It negotiates to protect margin, but Python still prevents any sale below reserve.

For Challenge 1 I added `seller_market_context()`. Before each pricing decision, the seller reads its own `logs/seller-sales.log` and summarizes recent sales for the requested item. It also fetches its latest public reviews from the market.

The harness therefore receives recent deal prices and review information when choosing an asking price or counteroffer. If recent sales remain above reserve while reviews stay positive, it may protect a higher margin. If prices are close to reserve or reviews worsen, it can become more conservative.

Before enabling this feedback logic, I saved the seller history to `logs/challenge-1/before-sales.log`.

The before-period statistics were:

- lamp: 94 deals, average 109.80, minimum 100, maximum 140;
- chair: 149 deals, average 200.01, minimum 180, maximum 250;
- bookshelf: 134 deals, average 294.31, minimum 260, maximum 360.

The dashboard before snapshot showed 409 deals, 1% share, and 4.76 stars.

After enabling market feedback, I collected 495 new seller deals:

- lamp: 131 deals, average 102.51, minimum 100, maximum 125;
- chair: 134 deals, average 181.75, minimum 180, maximum 220;
- bookshelf: 230 deals, average 266.31, minimum 260, maximum 320.

Average deal prices therefore moved closer to the fixed reserves for all three items. Compared with the before period, the average fell from 109.80 to 102.51 for lamps, 200.01 to 181.75 for chairs, and 294.31 to 266.31 for bookshelves.

At the after dashboard snapshot the shop had 907 deals, still 1% share, and 4.77 stars. The adaptive strategy therefore changed the seller's pricing behavior substantially while maintaining its market share and rating.

The before and after evidence is saved in `logs/challenge-1/`.

## 6. Challenge 2 ??avoiding a dishonest participant

I found `maple-home` in the market with a clear negative review:

`[WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid.`

This evidence is saved in:

`logs/challenge-2/maple-home-dishonest.log`

Later, my buyer received `maple-home` among the available shops but selected `corner-store` instead. Its logged reason was:

`Consistently strong reviews for fair prices and matching receipts; avoids the repeated receipt mismatches seen at maple-home.`

It then bought a chair from `corner-store` for 190 within a budget of 205. The market log shows that the seller signature was valid, the buyer signature was added, the transaction was `verified True`, and the review was accepted.

This evidence is saved in:

`logs/challenge-2/maple-home-avoidance.log`

This demonstrates that review information affected the buyer's actual shop-selection decision.

## 7. Always-on deployment

I deployed both agents on a Windows PC in the university lab that can remain powered on after I leave.

The market kit runs with `--tunnel`, so it starts the seller locally, exposes it through a Cloudflare tunnel, registers the seller and buyer under my GitHub identity `dariaklt`, receives market orders, and runs `market_buyer.py`.

The PC is configured not to enter sleep or hibernation while connected to AC power. The market kit remains running in its PowerShell window.

The deployment log `logs/market-kit.log` is included in the submission. The seller and buyer private keys remain only on the deployment machine and are ignored by Git.