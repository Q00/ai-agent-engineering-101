# Week 08 Midterm Report

Student ID: 26512073  
GitHub: Tulga0419  
Agent: Afford Shop

## What I built

I built a buyer and seller for Round 3. I chose the strategies and used Codex for coding, debugging, English wording, and diagram assistance. The buyer chooses shops and negotiates through A2A. My standalone seller serves its own ACP checkout and sends signed AP2 mandates to the market. The original kit runs the buyer's payment client and automatic restocking.

## Model, context, and tools

I used Codex CLI with gpt-6.1-sol for shop/SKU choice, stock selection, and negotiation decisions. The model returns JSON. Python checks prices, budgets, and allowed actions.

For each shop, the buyer uses up to ten reviews and its last eight trades. Payment records identify successful purchases. Reviews enter the prompt as untrusted evidence. Seller history records offers, counters, agreements, and cancellations. Confirmed trades help select stock.

My tools cover inventory filtering, FIFO selection, signed receipts, certificate and food-life checks, and stock-cost tracking. Confirmed purchases consume their actual cost. Refusals release reserved allowance. Uncertain requests keep their reservation. The harness saves raw attempts and waits after reported usage limits.

## My strategy

- Shared cumulative stock allowance of 4,000,000 credits for food and goods, with one unit per selected SKU in stock.
- Start at 5% above unit cost. After two counteroffers, allow 3% above cost.
- Food past the midpoint from supply to expiry may use the 3% minimum immediately.
- Offer the oldest available matching unit first. Exclude expired and held units.
- The buyer requests one discount at an affordable ask, then accepts an affordable counteroffer.
- Require a certificate and grades A/B/C. Food must last for the order's required time. Goods warranty is optional.

## Architecture

The one-page diagram uses six boxes: Memory, AI model, Buyer, Seller, Checkout, and Market. Paid outcomes feed later buyer choices. My Python rules guard the model's decisions.

My own checkout replaces the kit's seller checkout. It checks the original receipt, selects the agreed unit, and signs checkout details. Receipts and sessions are saved atomically. Payment is marked pending on disk before contacting the market. Completed payments survive restarts. Uncertain payments require market verification. The diagram was prepared with Codex assistance, using my selected strategies and simplified layout.

## Attempts and problems

I replaced the old fixed furniture reserves with actual unit costs. Failed stock requests initially consumed allowance, so I reconciled clear outcomes and added outcome tracking. Raw evidence remains preserved.

Model usage limits caused repeated failures. I added shared cooldown and stock-release handling. The standalone run also had three model timeouts and network errors near shutdown. These attempts are retained in logs. The development-attempt TAR.XZ archive contains unchanged raw files and a SHA-256 manifest.

## Results

Board snapshot at 2026-11-16T21:33:26.893070+09:00: Class Players rank 44 of 46, student percentile 0.044. The board confirms 347 seller settlements and 958 buyer purchases.

| Measure | Result |
| --- | ---: |
| Seller settlements | 347 |
| Buyer paid purchases | 958 |
| Sales through my standalone checkout | 19 |
| Standalone sales revenue | 229,614 credits |
| Standalone canceled checkouts | 8 |
| Pending payments at shutdown | 0 |

Standalone registration was at 08:19 KST on November 16. It stopped at 18:25. A real log line is:

```text
[acp] settled G0cf961e9 at 3094, ledger #7324040, unit u-c51beac3c948 via student checkout
```

A supply refusal said `supply is closed; the next restock window opens in 573 s`. Buyer logs also recorded `No deal within my budget`. Source evidence is under `logs/`, including `standalone-shop.log` and the board snapshot in `logs/setup/`.

All 93 offline tests pass, including temporary-key signature checks, price limits, cancellation, concurrent checkout, restarts, disk failures, and simulated registration. The current class repository has no `scripts/check_week08.py`.

## How to run

Use Python 3.14, install `requirements.txt` in `.venv`, authenticate Codex CLI, and make cloudflared available on PATH. Keep the existing private identities locally. From the repository root:

```powershell
# Buyer and seller with the original kit payment flow
powershell -NoProfile -ExecutionPolicy Bypass -File submissions/26512073/week-08/join_market.ps1

# Standalone seller and its own checkout, using existing stock
powershell -NoProfile -ExecutionPolicy Bypass -File submissions/26512073/week-08/join_market.ps1 -Standalone
```

Run one shop mode at a time. Enter the claim code privately at the prompt. `MARKET_OWNER` can supply it through the local environment. The standalone launcher sets `MARKET_URL`, `MARKET_DIR`, and its in-memory shop token.

Offline verification from the Week 8 folder:

```powershell
& ./.venv/Scripts/python.exe -m unittest test_agent -q
```

Forum posting remains unimplemented. Final ranking is taken when Round 3 ends.
