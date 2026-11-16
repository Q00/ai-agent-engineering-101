# Week 08 Midterm Report

Student ID: 26512073  
GitHub: Tulga0419  
Agent: Afford Shop

## What I built

I built a seller and buyer for Round 3. I chose the strategies with coding assistance. The seller handles A2A messages and signs receipts. The buyer chooses a shop, negotiates, and checks the receipt. The course kit currently handles ACP checkout, AP2 mandates, and market settlement.

## Model, context, and tools

The configured harness is Codex CLI with gpt-6.1-sol. The CLI uses its configured model. The model selects stock, chooses a shop and SKU, and returns negotiation decisions as JSON. Python checks the prices and allowed actions before using them.

The buyer reads up to ten reviews and its last eight trades for each shop. It gives weight to its own successful purchases. Payment records distinguish an agreement from a settled purchase. Reviews are treated as untrusted information. Seller history records offers, counters, agreements, and cancellations. Inventory and confirmed purchases help select stock.

My tools include the A2A buyer and seller, inventory filtering, FIFO selection, receipt signing, certificate and shelf-life checks, and restock cost tracking. The restock runner records each supply result. Actual purchases use their real cost. Refused requests release their reserved allowance. Uncertain requests keep their reservation.

The harness saves raw model attempts and waits after a reported usage limit. When the model is unavailable, the seller releases the held unit and the buyer records its own model problem.

## My strategy

- Stock food and goods with a shared cumulative allowance of 4,000,000 credits. Keep one unit per selected SKU in stock.
- Start at 5% above unit cost. Hold that price for two counteroffers, then allow 3% above cost.
- For food past the midpoint between supply and expiry, allow the 3% minimum immediately.
- Sell the oldest available matching unit first. Exclude expired and held units.
- The buyer requests one discount when the ask is affordable. It accepts an affordable follow-up counteroffer.
- Accept grades A/B/C with a certificate. Food must last for the order's required time. Goods warranty is optional in my policy.

## What I tried and changed

Earlier fixed furniture reserves could not price the new SKUs. I changed to actual unit cost. Failed supply requests originally consumed reserved allowance. I reconciled clear outcomes from the original logs and added outcome tracking. Ambiguous outcomes remain reserved.

Model usage limits caused repeated failures. I added a shared cooldown and clean cancellation. Some shops advertised stock but rejected the selected SKU. Tunnel failures also interrupted availability. The November 16 standalone run also recorded three 35-second model-call timeouts and network failures near 18:21, followed by SDK dispatcher warnings during shutdown. These attempts and raw logs are preserved.

## Results before submission

Captured 2026-11-16T07:20:15.419037+09:00. The local log contains 328 unique seller settlements and 958 unique buyer payments, counted by ledger ID. The market board snapshot at 2026-11-16T07:19:21.967930+09:00 shows 328 sales and 958 purchases. The Class Players rank is 44 of 46. Student percentile is 0.044. Board and local-log totals agree at this capture. Both sources are retained. These are interim results. Evidence is saved under `logs/setup/` with the `midterm-board` and `midterm-audit` prefixes.

Log examples show completed transactions.

```text
19:50:39 [acp] settled [3??? ??? ??? ??] ??? 20? ?? 20? ???? at 4469, ledger #6910589, unit u-0dd3c5534764 grade C
20:02:16 [acp] paid 52422 through https://shop.bluespot.team/acp, settled at ledger #6913958: [???by????] ??? 80? ??? ??? ???? S/SS, grade A
```

One supply refusal said `supply is closed; the next restock window opens in 573 s`. Buyer attempts also recorded `No deal within my budget`. Model allowance failures are recorded separately from seller reliability.

All 93 offline tests passed after adding independent checkout handlers, saved checkout state, and a standalone launcher. The repository has no `scripts/check_week08.py` at this checkout. Tests cover pricing, FIFO, buyer limits, receipts, stock accounting, cancellation, model cooldown, payment completion, and registration with a simulated market. The new checkout uses the seller's original receipt and signs its own checkout token. It sends signed mandates to the market and serializes completion and cancellation. Receipts and sessions are saved atomically in `.market/checkout-state.json`. A pending state is saved before contacting the market. Restart tests cover completed and canceled sessions, receipt holds, uncertain payments, and disk failures.

## Remaining requirements

This is a preparation draft. Independent ACP handlers, saved-state recovery, and a standalone registration launcher are implemented and checked locally. Standalone deployment registered in Round 3 on November 16 at 08:19 KST. Its public A2A and ACP endpoints passed verification, and inventory refresh loaded 49 units. Evidence is in `logs/standalone-shop.log`. The standalone run completed 19 market-settled sales before shutdown at 18:25 KST. Their checkout confirmations match the deployment log by ledger ID, unit, and amount. Revenue from this run was 229,614 credits. Eight checkout sessions were canceled and none remain payment-pending. The combined local log now contains 347 unique seller settlements and 958 buyer payments. Example: `[acp] settled G0cf961e9 at 3094, ledger #7324040, unit u-c51beac3c948 via student checkout`. The standalone mode sells existing stock. The original launcher runs the buyer and automatic restocking. An uncertain payment remains pending after restart and requires verification against the market before further action. An editable one-page `architecture.tex` and compiled `architecture.pdf` draft were prepared with Codex assistance from the implemented code. Student review and personalization are pending. The diagram shows buyer context feeding model-guided choices, guarded seller decisions, independent checkout, and saved payment state. The dashed feedback arrow shows paid outcomes influencing later buyer choices. Forum posting is not implemented. The deployed market runs described above used the kit's payment layer. The final diagram must show the implemented and deployed responsibilities accurately.

## How to run

Install `requirements.txt` in a local virtual environment. Authenticate Codex CLI and keep the existing identity keys private. From the repository root, run this in PowerShell and enter the market claim code at the private prompt.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File submissions/26512073/week-08/join_market.ps1
```
