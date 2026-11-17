# Week 08 Midterm Report — Round 3 Agent Market

Student ID: 25512084  
Market identity: `dariaklt`

## 1. Components: model, context, and tools

### 1.1 Model and harness

The repository retains the Codex-based harness from Weeks 06/07. The configured model is Codex CLI 0.163.0 with `gpt-6-luna`, as recorded in `agent.json`.

In the final Round 3 architecture, however, the high-frequency market-critical decisions are implemented as deterministic Python policies. The model/harness remains available for the general agent path, while seller pricing, market-buyer selection and negotiation guards, receipt validation, and restocking are code-enforced.

This separation was deliberate: hard economic and protocol constraints such as price floors, acceptable-SKU checks, mandate caps, and inventory limits should be reproducible and auditable rather than left only to model prompting.

The Windows deployment uses the virtual-environment Python executable directly in `agent.json` so the same commands run consistently from the lab PC.

### 1.2 Context and memory

The agents use the Round 3 `.market/` state written by `market_kit.py`.

The buyer reads:

- `.market/shops.json` for currently registered shops, reviews, and compatible inventory;
- `.market/order.json` for the current order and acceptable SKUs;
- persistent buyer memory for prior shop outcomes and prices;
- recent quotes from several shops to build a market benchmark.

The seller reads:

- `.market/inventory.json` for exact units, cost, grade, age, and expiry;
- `.market/catalog.json` for list price and product type;
- in-memory A2A task state for open negotiations.

The restocking strategy reads:

- inventory and wallet state;
- catalog information;
- `demand.json` / observed order frequency to favor categories that are actually requested.

This context is intentionally separated from the decision rules. Market state can change continuously, while price floors, mandate safety, and receipt validation remain deterministic.

### 1.3 Tools and protocol actions

The Week 08 system uses several protocol-facing tools and actions:

1. **A2A negotiation** — `seller_r3.py` exposes the seller through an A2A Agent Card and handles task-based price negotiation.
2. **ACP checkout** — the Round 3 kit exposes the shop checkout endpoint and creates checkout sessions from the seller's signed receipt.
3. **AP2-style payment mandates** — the buyer never completes a checkout above the hidden mandate cap. The kit verifies the checkout against the order and mandate before payment.
4. **Receipt signing** — the seller signs canonical receipt fields using Ed25519.
5. **Selective market probing** — `market_buyer.py` probes several compatible shops, compares quotes, and negotiates only with candidates that satisfy the buyer's guardrails.
6. **Demand-driven restocking** — `restock_r3.py` chooses new stock from market demand rather than repeatedly buying only a few fixed products.

Private key files remain local and are excluded from Git.

## 2. Original architecture and how it differs from the kit default

The architecture is shown in `architecture.tex` and the built `architecture.pdf`.

The default kit mainly coordinates registration, market orders, ACP checkout, AP2 payment, and settlement. My structure adds three custom decision layers around it:

- a **profit-aware seller** that prices from the actual unit cost, grade, inventory age, and expiry;
- a **selective-surplus market buyer** that filters by compatible inventory, compares several shops, uses market and memory-based price information, and validates the final receipt;
- a **demand-driven restocker** that changes future inventory based on observed demand.

The market kit remains the protocol boundary. It talks to the market, writes `.market/` context, serves ACP for the shop, and settles AP2 payments. My agents consume that context and make their own seller, buyer, and restocking decisions.

This differs from the simpler kit behavior because the buyer does not choose a shop blindly, the seller does not use a single fixed price, and restocking is not concentrated on a small static SKU set.

## 3. Seller design

The final seller is `seller_r3.py`.

For each available unit, the seller computes a policy from the actual unit cost and the catalog list price. It uses grade-dependent minimum profit margins and never intentionally sells below cost.

The final margin policy is approximately:

- grade A: minimum margin 8%;
- grade B: minimum margin 6%;
- grade C: minimum margin 4%;
- fallback: 5%.

The seller derives:

- `floor`: the minimum profitable price;
- `target`: the preferred deal price;
- `ask`: the initial asking price.

Negotiation is short and deterministic:

1. accept immediately when the buyer offers at least the target;
2. after one counter, accept a still-profitable offer at or above the floor;
3. otherwise counter first toward the target and then toward the floor;
4. reject offers that remain below the allowed level.

The strategy logs the unit SKU, cost, floor, target, ask, grade, product kind, and expiry, for example with `[strategy] open`, `[strategy] COUNTER`, and `[strategy] ACCEPT` records.

### Quality claims and warranty

A later Round 3 fix added explicit quality information to the seller's receipt terms:

- `cert = True`;
- `claim` equal to the real grade of the selected unit;
- `warranty_hours = 6` for goods.

Before this change, the leaderboard did not have enough seller-provided data to count grade honesty and goods could not satisfy buyers that required a six-hour warranty. After enabling the grade claim, the shop immediately began recording honest claims (for example, the leaderboard changed from no honesty value to `2/2` shortly after restart).

## 4. Buyer design

The final buyer is `market_buyer.py` and identifies itself in logs as the `selective-surplus buyer`.

### 4.1 Compatible-shop filtering

The first important Round 3 improvement was to use the `offers` metadata that the kit adds to each shop for the current order.

Instead of ranking every registered shop, the buyer first filters to shops with at least one SKU in the order's acceptable set. This avoids spending probes on shops that cannot satisfy the order.

### 4.2 Exact acceptable SKU negotiation

A major failure mode was item mismatch. A shop could be valid for the category but still choose a SKU that was not acceptable for the current order.

I changed the buyer prompt so the seller is explicitly asked to quote one of the exact acceptable SKUs supplied by the order.

Before this change, item mismatch was one of the dominant causes of failed receipts. In a fresh diagnostic sample after the exact-SKU change, the buyer produced 60 deals, 10 walkaways, and only one mismatch, compared with a much lower deal rate and many more mismatches before the fix.

### 4.3 Multi-shop probing and price guard

The buyer probes up to four shops and orders candidates by quoted price with reputation used to break close ties.

It computes a price ceiling from the current market quotes and past observations. The buyer makes an initial offer below its maximum and may make a second offer up to the allowed maximum. It never intentionally pays above the order budget.

The buyer also skips extreme asks rather than wasting negotiation rounds.

Typical log messages include:

`[guard] quotes=[...] max_pay=...`

`[offer] <shop>: ...`

`[skip] <shop>: ask=... too far above max_pay=...`

### 4.4 Checkout acceptance policy

The buyer validates the returned receipt and uses Round 3 checkout constraints.

For perishable products it checks remaining shelf life against the order's intended use window. For goods it can require a six-hour warranty. The kit then checks these conditions again at ACP checkout before AP2 payment.

Examples observed in the deployment log include:

`[acp] not paying: warranty 0 h, the policy asks for 6`

and

`[acp] not paying: 23.48 h of shelf life left, the policy asks for 24.0`

These are intentional safe refusals, not crashes.

## 5. Restocking design

The original restocking behavior concentrated inventory in a very small number of SKUs. That reduced coverage because many market orders had no compatible stock.

I replaced it with `restock_r3.py`, a demand-driven diversified strategy.

The final strategy:

- limits total inventory instead of accumulating stock without bound;
- limits each restock budget to a fraction of available balance;
- uses observed demand / order frequency to prioritize categories;
- diversifies across requested categories instead of repeatedly buying the same few SKUs;
- avoids risky exploratory food inventory with very short shelf life;
- avoids excessively expensive exploratory stock.

After this change, compatible order coverage improved and the seller began receiving sales across a wider set of categories.

## 6. Failures, experiments, and fixes

### 6.1 Item mismatch

Earlier buyer versions often completed A2A negotiation for an item that did not satisfy the current order.

One example in the log ended with:

`receipt ... does not fit the order, not signing`

and the market marked the transaction as failed because `item_matches`, `sig_buyer_ok`, and `settled` did not pass.

I fixed this by filtering to shops with compatible `offers` and by sending exact acceptable SKU identifiers during negotiation.

### 6.2 Unreachable shops

Some registered shops could not be reached during probing. The buyer treats this as a candidate failure and continues with other shops rather than failing the whole order.

Example:

`[probe] moss-and-oak: unreachable (HTTPStatusError)`

### 6.3 No acceptable quote within the mandate/value limit

The buyer intentionally walks away when every reachable offer is too expensive.

Example:

`[walk] ... no acceptable deal. No reachable quote within mandate cap.`

This is preferable to forcing a purchase that violates the user's delegation.

### 6.4 Margin-Saved experiment

After the leaderboard changed the buyer `Saved` metric to a margin-based definition, I tested a stricter pre-negotiation margin target.

A target of 65% reduced the fresh deal rate to about 20%. Lowering the target to 60% did not solve the problem; the observed fresh deal rate was still about 16%.

The experiment was therefore rolled back. The previous exact-SKU buyer had much higher transaction coverage and, after running longer, its leaderboard `Saved` value continued to improve without the strict margin guard.

This rollback was an important design decision: optimizing one metric too aggressively reduced the number of useful settled deals.

### 6.5 Missing seller warranty / quality terms

The seller originally signed the normal receipt fields but did not explicitly send quality claims or warranty terms. This caused missed quality evidence and made some goods checkouts incompatible with buyers requiring warranty.

I added the true grade claim, certificate flag, and six-hour warranty for goods. This preserved the existing receipt signature logic while allowing the Round 3 kit to place the extra seller-provided terms into checkout.

## 7. Round 3 results

The shop and buyer both exceeded the minimum requirement of 20 settled Round 3 deals by a large margin.

The submission-time market snapshot below was saved on **2026-11-17 at 15:43 KST**.

### Players

In the saved Players table snapshot:

- Player: `dariaklt`
- Players rank shown: **#48**
- Total: **86**
- Shop ALL: **39**
- Buyer ALL: **47**

The assignment specifies that the final graded Round 3 rank is taken from the board's **Class** view when the round ends. This report records the rank visible in the saved submission-time snapshot.

### Shop

At the same submission-time snapshot:

- counted shop deals: **2,234**
- receipts: **4,370**
- receipts with order: **4,370**
- reviews: **2,216**
- stars: **4.57**
- Shop ALL: **39**
- shop-side rank: **#42**
- cash: **9,765,207**
- stock: **221,703**
- book: **9,986,910**
- revenue: **34,549,567**

The seller was online again on the final submission day after the lab PC had been accidentally powered off and the Cloudflare tunnel, shop, and buyer were restarted.

### Buyer

At the same submission-time snapshot:

- Buyer ALL: **47**
- buyer-side rank: **#51**
- Saved: **64.7%**
- surplus: **85,887,391**
- forum rating: **0.92**
- deals: **7,689**
- served: **7,393 / 7,689**

The buyer's Saved score increased substantially over the earlier Round 3 runs while maintaining thousands of settled transactions.

### Refusal reasons observed in the deployment log

The main safe refusal modes were visible directly in `logs/market-kit.log`.

Examples include:

- `[walk] ... no acceptable deal. No reachable quote within mandate cap.`
- `[probe] moss-and-oak: unreachable (HTTPStatusError)`
- `[acp] not paying: warranty 0 h, the policy asks for 6`
- `[acp] not paying: 23.48 h of shelf life left, the policy asks for 24.0`

These refusals are intentional safety behavior. The buyer does not force settlement when no reachable offer fits its value / mandate ceiling, when a shop is unreachable, or when ACP checkout fails the requested warranty or shelf-life policy.

## 8. Deployment

The final system runs on a Windows lab PC.

I run the Cloudflare tunnel, shop registration, and buyer registration in separate PowerShell processes. The shop uses a fixed local port and the current Cloudflare quick-tunnel URL is passed to the kit as its public URL.

The deployment uses the GitHub-linked market identity `dariaklt`.

The machine was configured for long-running deployment, although one accidental shutdown on the final day stopped the processes. I restarted the Cloudflare tunnel, shop, and buyer and re-registered both agents before submission.

The submission includes `logs/market-kit.log` from the deployment machine. Private keys and `.market/` runtime state are not committed.

## 9. Reproducibility and submitted files

The Week 08 folder contains the files needed to reproduce the custom architecture:

- `agent.json`
- `harness.py`
- `buyer.py`
- `market_buyer.py`
- `seller_r3.py`
- `restock_r3.py`
- `REPORT.md`
- `architecture.tex`
- `architecture.pdf`
- `logs/market-kit.log`

Temporary backup files, `.market/`, `__pycache__/`, and private key files are not part of the submission.

The architecture figure summarizes the runtime flow:

market order -> kit context -> selective buyer / seller / restocker -> A2A negotiation -> ACP checkout -> AP2 payment -> market settlement and ledger.
