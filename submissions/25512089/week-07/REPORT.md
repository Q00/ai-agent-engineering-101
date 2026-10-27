# Week 07 Report — An Agent Market: Reputation and Verification

**Student ID:** 25512089
**GitHub / market identity:** `ChanArun` / `chanarun`
**Environment:** Windows PowerShell, Python virtual environment, A2A SDK, GroqCloud (`openai/gpt-oss-120b`), A2A JSON-RPC 1.0
**Fixed Week 07 test:** **25/25 passed — PASS** (after adding rate-limit fallbacks)

## 1. How the buyer selects a shop and remembers experience

My `market_buyer.py` reads the shop list and up to the ten latest available reviews per shop. Before a transaction, it calculates a reputation summary for every shop: average stars, count of low-star reviews (one or two stars), previous successful deals, previous no-deals, and incidents recorded by my own buyer. Receipt mismatches and other integrity incidents carry a stronger penalty than ordinary no-deals. Buyer-owned API outages and network/service failures are tracked separately; they are not automatically treated as proof that a seller is dishonest.

The buyer first excludes shops with low-star reviews or its own recorded receipt-integrity incidents when safer alternatives exist. It sorts eligible shops by a numerical reputation score, limits the selection context to the best 12, and asks the model to choose from that shortlist. If model selection is unavailable due to a Groq API limit, the fallback picks the highest-scoring eligible shop. The choice and reason are logged with `[REPUTATION]` and `[CHOOSE]` records.

Memory is stored locally in **`.market/buyer-memory.json`**, as a JSON array of up to 1,000 recent transactions. Entries include `shop`, `item`, `budget`, `price`, `result`, `stars`, `review`, `picked_because`, and `timestamp_utc`. This is persistent local state, not a file to commit: `.market/` can also contain sensitive kit state.

The buyer enforces its item budget in code, uses A2A Version 1.0, continues negotiations with the same `taskId`, and checks the final receipt's item, price, and consistency with the last agreed offer. It emits the market kit's required final JSON object containing `shop`, `receipt`, `stars`, and `review`.

## 2. How reviews are provided to the model harness

Passing every full review of every market participant to the model would create a large and changing context. Instead, `market_buyer.py` builds a compact `reputation_summary` for each shop using public reviews and the buyer's own memory. Each summary contains counts, average rating, risk score, and up to three short review excerpts. The model sees only the eligible top-12 summaries and must return an exact shop name and a short reason. Deterministic safeguards ensure that an ineligible or nonexistent shop is not selected.

For shop-side adaptive pricing, the seller separately reads its **own** recent signed sales from `.market/seller-trades.jsonl` and its locally saved dashboard review snapshot from `.market/own-review-summary.json`. The latter is a manually recorded snapshot, not a continuously refreshed live review feed. Every new opening-price adjustment is written to `.market/pricing-decisions.jsonl` and logged as `[ADAPTIVE PRICE]`.

## 3. What happened in Rounds 0, 1, and 2

| Round | Market rule / implementation | Evidence available for this report |
|---|---|---|
| Round 0 | Initial market trades, before reputation-aware buyer and review-based selection | No verified personal Round 0 dashboard totals retained in the supplied snapshots; no numbers invented. |
| Round 1 | Reputation-aware `market_buyer` selects shops and leaves reviews | Shop selection, review summarization, and memory are implemented; no verified personal Round 1 totals retained in the supplied snapshots. |
| Round 2 | Only receipts backed by an order and valid signatures count | Dashboard snapshots, local receipts, seller signing code, live logs, and the official 25/25 test. |

The two captured **Round 2** dashboard snapshots for `chanarun` were:

| Measure | Before adaptive-pricing change | Later snapshot |
|---|---:|---:|
| Dashboard counted deals | 114 | 120 |
| Dashboard reviews | 114 | 120 |
| Average rating (stars) | 4.55 | 4.56 |
| Displayed market share | 0% | 0% |

Thus, the observed counted-deal total rose by six between the snapshots. The dashboard's displayed `0%` is rounded or display-limited; the total market-wide number of counted deals was not captured, so **exact market share cannot be computed** and no claim of a zero true share is made. The snapshots also do not identify how many counted deals were initiated by my buyer rather than received by my shop. Buyer-side counted-deal confirmation must be checked independently against market ledger lines.

The initial Round 2 seller log was archived locally before the next experiment. Its 114 unique signed-receipt task IDs corresponded to an average signed receipt price of 201.75; the new local log contained six unique signed receipts averaging 230.50. **Local signed receipts are not automatically equivalent to market-counted deals**, and these two logs cover different lengths of time.

## 4. Receipt signatures: what needed fixing and how they work

A normal Week 06 completed-trade receipt was not enough for Round 2: the market also checks cryptographic signatures and an order-linked transaction. The updated `seller.py` creates an Ed25519 signature using the registered private `shop_key.pem`. It signs canonical JSON containing exactly `shop`, `item`, `price`, and `task` (sorted keys, compact separators, UTF-8), then adds the Base64-encoded signature as `sig_seller`. The receipt remains an A2A data-part artifact attached to the completed task. The buyer returns the receipt as received rather than synthesizing a replacement signature.

The official Week 07 test passed **25/25**, including the signed-receipt checks, after the updates. I do not have a retained initial failed-signature trace to quote, so I do not claim a specific signature-error line. The market kit performs the authoritative registered-key/signature and order checks; my buyer's own receipt checks cover price, item, task continuation, and agreement consistency, not a separate cryptographic verification implementation.

A separate runtime failure was Groq `429` / cooldown errors, which interrupted the seller before adaptive pricing and interrupted buyer negotiations before an offer was sent. I added reserve-respecting seller fallback decisions and budget-respecting buyer fallback offers for model quota outages. An intervening test passed 19/25 because three buyer-deal tests and their three protocol checks failed. After the buyer fallback was installed and the virtual environment was activated, the official market test passed **25/25** again. This is recorded as a runtime reliability repair, not as evidence that the original signature code itself was wrong.

## 5. Required challenges

### Challenge 1 — Seller changes its own asking prices

The seller uses `adaptive_pricing.py`, which combines two observable signals. The saved review snapshot (`114` reviews, `4.55` average stars) contributed **+1.5%** to its opening-price adjustment. Once there were at least three recent signed bookshelf sales, the local sales-history rule contributed another **+1.5%**, for a total rule adjustment of **+3.0%**. A live record shows `bookshelf`, reserve `260`, model/base ask `281`, and final ask `289` (the numerical final ask is rounded). The recent bookshelf sale average in that record was `276.67` based on three local trades. Reserve limits were still enforced.

These were the item-level averages calculated from deduplicated local signed receipts:

| Item | Before: receipts | Before: average price | After: receipts | After: average price |
|---|---:|---:|---:|---:|
| Bookshelf | 47 | 272.77 | 4 | 274.25 |
| Chair | 37 | 187.97 | 1 | 180.00 |
| Lamp | 30 | 107.50 | 1 | 106.00 |
| **All items** | **114** | **201.75** | **6** | **230.50** |

The overall local receipt average increased by `28.75` (about `14.25%`), but it is **not a causal estimate** of the adaptive-pricing effect. The after period had proportionally more expensive bookshelves and only six receipts. Within items, the bookshelf mean rose slightly while the chair and lamp means declined. The dashboard still displayed `0%` share in both snapshots. These data demonstrate **price adaptation in operation**, not a proven improvement in market share or price performance.

Evidence: `logs/challenge-1/before.json`, `logs/challenge-1/after.json`, `logs/challenge-1/item-price-comparison.csv` (if exported), the running market kit log, and local `.market/pricing-decisions.jsonl` / `.market/seller-trades.jsonl` (not committed). The old 138,937,583-byte market log was archived outside the repository to avoid committing an oversized file.

### Challenge 2 — Avoid a suspicious participant

The locally downloaded market review snapshot contained a shop named `maple-home` with **eight low-star reviews**, including reports that signed receipt amounts differed from agreed prices. My buyer's risk model penalized those reviews and excluded the shop from the actual 12-shop eligible selection pool. The Round 2 avoidance audit reported:

- `maple-home` low-star reviews: **8**;
- `maple-home` risk score: **−44.0**;
- excluded from actual 12-shop selection pool: **True**;
- **none** of the last ten recorded real buyer choices selected `maple-home`;
- audit result: **PASS**.

This demonstrates risk-aware avoidance from the buyer's observed reviews and its selection logs. It **does not prove** that the market operator officially designated `maple-home` as fraudulent. During some live selections, Groq was rate-limited, so the deterministic reputation-score fallback made the selection rather than the model. The official `buyer_reads_reviews` test selected the well-reviewed shop in **3/3** runs.

Evidence: `logs/challenge-2/round2-avoidance-audit.log` and `[REPUTATION]` / `[CHOOSE]` entries in `logs/market-kit.log`.

## 6. Deployment and reproducibility

I tested and ran the agents from **Windows PowerShell on my own PC**, in the Python virtual environment at `C:\Users\ARUN\a2a-lab\.venv`. The market kit launched the shop locally, exposed it through a Cloudflare Quick Tunnel (`--tunnel`), registered the shop/buyer as `chanarun`, and fetched new orders. The seller Agent Card answered `GET /.well-known/agent-card.json` with HTTP 200 during local inspection, and live market interactions produced signed receipts and adaptive-pricing records.

Run from the repository root, with the virtual environment activated and the required secrets supplied privately as environment variables:

```powershell
& "C:\Users\ARUN\a2a-lab\.venv\Scripts\Activate.ps1"
$env:Path = "C:\Program Files (x86)\cloudflared;$env:Path"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# OPENAI_API_KEY and MARKET_OWNER must be set securely in this shell.
python scripts\market_kit.py join --dir submissions\25512089\week-07 --market https://market.ourolabs.ai --name chanarun --tunnel
```

Official test command:

```powershell
python scripts\market_test_week07.py submissions\25512089\week-07
```

**Deployment limitation:** a laptop/desktop using a Quick Tunnel is reachable only while the host, market kit, and tunnel are running. A verified VPS or other machine that remains on after closing my laptop has **not** been established in the evidence available here. Therefore I do **not** claim that the assignment's always-on-at-deadline requirement is already satisfied. The market-kit process must remain up on an always-on machine and its registered endpoint and buyer must be checked again before the deadline.

Do not commit `OPENAI_API_KEY`, `MARKET_OWNER`, `.market/`, `*.pem`, private keys, backups, or archived oversized logs. Only safe source files, `agent.json`, test evidence, compact market logs, challenge evidence, and this report belong in the Week 07 submission. Confirm that the current `logs/market-kit.log` contains registration and actual ledger/order activity, and that `test-report.json` exists, before opening the PR.

## Final status at the recorded snapshots

- Official Week 07 market test: **25/25 PASS**.
- Reputation-aware buyer, persistent memory, budget limits, A2A task continuation, signed seller receipts: **implemented and tested**.
- Required seller adaptive-pricing change: **observed in a live price-decision record**.
- Required suspicious-shop avoidance: **Round 2 audit PASS**.
- Dashboard counted deals: **114 → 120**; displayed market share **0% → 0%**; exact share unknown.
- Outstanding verification: Round 0/1 personal figures (if accessible), buyer-side counted deal, and genuinely always-on hosting at the deadline.
