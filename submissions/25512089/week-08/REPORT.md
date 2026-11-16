# Week 08 Midterm — Round 3 Agent Market

**Student ID:** 25512089
**Agent / shop name:** `chanarun`
**Submission path:** `submissions/25512089/week-08/`
**Status:** **INTERIM — OFFICIAL RANK AND FINAL MARKET COUNTS PENDING**
**Round:** 3 (Market Kit v4)

## 1. Goal and system summary

I extended my Week 07 A2A seller and reputation-aware buyer for Round 3 of the course market. The system uses a GroqCloud model for negotiation, but it also applies deterministic checks for inventory, budget, product selection, signatures and checkout. I added my own inventory-selection, restock and post-settlement review modules on top of the instructor's Market Kit v4. The goal is to trade real stocked products under ACP/AP2 settlement rules rather than count signed receipts alone.

My design has **three connected parts**: (1) model-driven dialogue, (2) state and memory used to decide what to do, and (3) tools and guards used to execute only eligible actions. The architecture is in `architecture.tex` / `architecture.pdf`.

## 2. Model component

- **Model:** GroqCloud `openai/gpt-oss-120b`, accessed through an OpenAI-compatible client (`harness.py`).
- **Seller:** `seller_opening` and `seller_turn` handle price negotiation. Deterministic safeguards enforce the reserve price and inventory selection even when a model responds incorrectly or rate-limits.
- **Buyer:** `market_buyer.py` uses historical risk-aware scoring to select among eligible shops, while retaining model-assisted hybrid negotiation. When the model is unavailable or rate-limited, the buyer implements constrained fallback behavior rather than treating the model response as authoritative.
- **Division of responsibility:** the model proposes negotiation actions; Python validates eligibility and pricing; Market Kit performs the payment and supply API calls.


**Current Buyer v3 implementation:** The active buyer includes
historical risk-aware shop selection, using observed settlement
verification outcomes and previous failure evidence. Its negotiation
component retains the earlier hybrid negotiation behavior.
These signals are historical evidence, not guarantees of success.

## 3. Context and memory component

- **Current context:** the Round 3 Kit synchronizes `inventory.json`, `catalog.json`, `demand.json`, `forum.json`, `shops.json` and `order.json` under `.market/` where applicable. These are runtime state, not submission secrets.
- **Inventory context:** `round3_inventory.py` chooses a currently stocked unit, preferring an exact SKU; it checks expiry for food and uses cost and list price to derive a reserve. Snapshot availability is rechecked; the market is authoritative for final settlement.
- **Buyer context:** `market_buyer.py` reads the order's `acceptable` SKUs and shops' `offers`, so it does not negotiate for an unavailable or ineligible product just because the name seems similar.
- **Buyer memory:** `market_buyer.py` reads/writes a bounded history of past negotiations (up to 1,000 entries), including outcome, price, review and shop. Past deals and reputation contribute to shop selection. Public reviews are treated as claims rather than confirmed facts.
- **Seller feedback:** `adaptive_pricing.py` adjusts asking prices using recent own trades and reviews while respecting the deterministic reserve.

## 4. Tools and actions I implemented

| Module | Tool / role | Safety or design choice |
| --- | --- | --- |
| `seller.py` | A2A seller; inventory-aware receipt negotiation | Ed25519-signed receipt; deterministic minimum reserve; SKU/unit selection |
| `market_buyer.py` | Eligible-shop discovery, A2A negotiation, receipt verification | Selects `acceptable` SKU in stock, checks price against order budget |
| `round3_inventory.py` | Read-only inventory and price guard | Exact SKU first; excludes expired food; no independent spending |
| `adaptive_pricing.py` | Seller ask adjustment | Uses seller experience and review signals, stays above reserve |
| `restock_v4.py` | Supply-decision hook | Checks supply window; limits estimated spend; prefers nonperishable goods; no direct API call |
| `review.py` | Post-settlement review hook | Reviews observed settlement fields rather than unverified quality claims |
| `harness.py` | LLM client / structured decision calls | Central model interface and retry/fallback paths |
| Course Market Kit v4 | ACP/AP2 checkout, supply and settlement | Market checks actual paid/settled status; kit handles payment API |

The `restock.py` heuristic is conservative, **not** a learned demand forecast. It uses limited signals from completed purchases and public forum posts; neither proves future demand. In particular, `demand.json` is a list of order categories, not their sales probabilities.

## 5. Architecture and how it differs from the default Kit

The instructor's Kit supplies registration, proxy, ACP/AP2 endpoints, payment/settlement, state synchronization and optional hooks. My added structure separates the **model layer**, **buyer memory / reputation layer**, **inventory-aware seller guard**, **restock policy**, **review policy**, and **adaptive pricing**. The market state is a shared observation, but model outputs never directly bypass order eligibility, inventory, reserve or the Kit's checkout checks.

The single-page LaTeX architecture figure describes the data and control flow. Please review/adjust the figure so it matches your own explanation before submission; the course asks for a figure the student draws and can explain.

## 6. Tests and evidence

- **Final offline validation (November 14, 2026):** `python -m unittest -v test_round3_offline test_restock_v2 test_restock_v3` -> **30/30 PASS** (9 Round 3, 9 Restock v2, 12 Restock v3).

- Offline compatibility tests: `python -m unittest -v test_round3_offline.py` -> **9 tests passed** on November 4, 2026.
- Model connectivity: GroqCloud API request to `openai/gpt-oss-120b` returned `OK.` in a live test.
- Market registration: `market_kit 4`; both shop and buyer registered as `chanarun` for Round 3; A2A Agent Card served successfully.
- Live supply evidence: multiple `[supply] ... balance ...` records, beginning around 11:43 KST on November 4, 2026.
- Live shop-settlement evidence sampled from log: `#2361252`, `#2361702`, `#2361965` (lines beginning `[acp] settled`).
- Live buyer-settlement evidence sampled from log: `#2358792`, `#2358879`, `#2359045`, `#2359252` (lines beginning `[acp] paid ... settled`).

**Observed log-based snapshot on November 14, 2026 (NOT FINAL):**

| Metric | Observed | Requirement | To update |
| --- | ---: | ---: | --- |
| Buyer unique settled ledger IDs | **2,919** | >= 20 | Verify against Players/ledger and update at PR |
| Shop unique settled ledger IDs | **10** | >= 20 | Continue selling and update at PR |
| Inventory at one observation | **3 units** | — | Dynamic; do not confuse with final sales |
| Shop balance at one observation | **5,115,029 market credits** | — | Dynamic; this is not net profit |
| Players rank/percentile | **NOT YET RECORDED** | Ranking rubric | Enter board snapshot at submission |

Counts above come from unique ledger IDs observed across `logs/market-kit.log` and `logs/market-kit.log.1`. The definitive Round 3 counts should be cross-checked against the market board or ledger. The course's final ranking is taken at Round 3 close, not when this draft was written.

### Observed refusals and no-deals

- `[market] ledger #2358701: no deal, stars 3, verified False` — a negotiation did not produce a paid transaction.
- `TASK_STATE_REJECTED` — the other shop declined an attempted negotiation.
- `[acp] not paying: 74.45 h of shelf life left, the policy asks for 84.0` — the buyer rejected a delivery that did not satisfy its minimum remaining shelf-life policy. This is a desirable safety refusal, not a settled deal.
- `[supply] ... refused: 429` — the restock window was closed; there was no purchase from that request.

**Before submitting:** replace/add excerpts from the final log with actual timestamps, refusal counts and representative reasons, rather than relying only on the examples above.

## 7. Limitations and next improvements

- The conservative restock heuristic does not optimize real demand or guarantee profits. It may buy slow-moving goods, and goods can incur storage costs or warranty refunds.
- Reputation signals and forum text can be noisy or strategically manipulated; seller selection should not accept them as ground truth.
- A successful signature or completed A2A task is **not** a settlement; only the ACP/AP2 market ledger counts.
- The seller's availability is based on a synchronized inventory snapshot; concurrent buyers can still compete for the same unit and the Kit must reject invalid settlement.
- Running `--orders 0` causes the buyer to keep processing orders; a separate `--shop-only` mode can be used to focus on shop sales after the buyer baseline has been met.

## 8. Submission checklist

- [ ] Confirm at least 20 **shop** deals and 20 **buyer** deals settled in Round 3 on market records.
- [ ] Update final buyer/shop counts, refusal examples and Players board rank in this report.
- [ ] Review the architecture figure and modify it to match what I actually implemented and can explain.
- [ ] Include `architecture.tex` and a successfully built one-page `architecture.pdf`.
- [ ] Commit changed source files and genuine `logs/market-kit.log` evidence under `logs/`.
- [ ] Check that **no** `*.pem`, API tokens, `MARKET_OWNER` claim code, or other credentials are staged.
- [ ] Do not squash history; push only branch `week-08` and open the PR before November 17, 2026 at 23:59 KST.

## 9. Reproduction commands (Windows PowerShell)

```powershell
$repo = 'C:\Users\ARUN\GitHub\ai-agent-engineering-101-week08'
$target = "$repo\submissions\25512089\week-08"
$env:PATH = "$repo\.venv\Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8:replace'
cd $target
python -m unittest -v test_round3_offline.py
# Only run the live kit when the existing session is stopped and
# OPENAI_API_KEY and MARKET_OWNER have been securely set in this PowerShell.
# python "$repo\scripts\market_kit.py" join --dir "$target" --market https://market.ourolabs.ai --name chanarun --tunnel --orders 0
```

## 10. Updated Round 3 and Week 10 Evidence

### Interim Round 3 results — November 14, 2026

Unique settlement ledger IDs observed across the available
market-kit.log and market-kit.log.1 files:

- Buyer: 2,919 settled transactions.
- Shop: 10 settled transactions.
- Required baseline: at least 20 for each role.
- Official Round 3 leaderboard snapshot is documented in Section 11.

These are log-based observations, not final market rankings.

The 10 observed shop settlements comprise seven curtain-rod
sales and three cutlery-set sales. Sold-unit acquisition costs
have not yet been verified, so net profit is not claimed.

### Week 10 proactive action

I implemented week10_first_move.py to inspect the synchronized
inventory, demand, catalog and forum data, then select an
in-stock product for a proactive shop forum post.

- Selected SKU: G83ce0a8b
- Demand category match: True
- Snapshot inventory: 1 unit
- Market response: HTTP 200
- Forum post ID: 121054
- Evidence: logs/week10_forum_response.jsonl
- Baseline: logs/week10_first_move_baseline.json

This action was executed manually as a proof of concept.
A fully automatic recurring trigger has not yet been implemented.
Likes and replies have not yet been measured.

### Current restock policy

The active restock_v4.py uses conservative risk limits:
- Cold-start inventory cap: 3 units.
- Unsold-cost cap: 55,000 market credits.
- Minimum cash floor: 4,700,000 market credits.
- Historical strategy is preferred when sufficient evidence exists.

These limits reduce inventory exposure but do not guarantee
profitable sales.

## 11. Official Round 3 Leaderboard Snapshot

Observed on November 14, 2026, from the Agent Market
Round 3 Leaderboard. This is an interim snapshot.

### Class Players

- GitHub account: ChanArun
- Class rank: 44 of 46 players
- Class percentile: 4
- Players Total: 28
- Players Shop component: 11
- Players Buyer component: 16

### Shop: Arun A2A Seller (chanarun)

- Shop ALL: 21
- Counted settled deals: 91
- Reviews: 91
- Stars: 4.34
- Cash (net of starting capital): 114,952
- Unsold stock cost: 31,108
- Book: 146,060
- Revenue: 1,867,866

### Buyer: chanarun

- Buyer ALL: 24
- Settled deals: 18,349
- Served: 17,942 / 18,349
- Saved: 60.5%
- Buyer forum posts: 0
- Forum rating: unavailable

### Baseline verification

- Shop: 91 >= 20. PASS.
- Buyer: 18,349 >= 20. PASS.

Earlier local log-based observations (2,919 buyer and
10 shop settlements) covered only the available log files.
The market leaderboard is used for the overall Round 3
snapshot.

The course determines the final Class ranking when
Round 3 closes on November 17, 2026.
