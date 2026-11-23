# Week 08 Midterm — CREDENCE: An Inventory-Backed Reputation Trading Agent

Student: **26510128** · GitHub: **JangSeongWoong**

## Scope and source of truth

This is the **current Week 08 Round 3 midterm** (model/context/tools 40%,
original architecture 30%, final Players percentile 30%). It is **not**
the obsolete reproduction-report version of the midterm.

Official lecture: https://wpti.dev/ai-agent-engineering-101/week-08.html

Round 3 rules and patch 3.5: https://market.ourolabs.ai/rules
and https://market.ourolabs.ai/agent.md

The official course **market_kit.py v4** owns inventory supply, market
registration, ACP checkout and AP2 settlement. Our code owns SKU choice,
seller offer price, buyer trust, model prompts, bounded restocking and
post-settlement feedback. Do **not** manually forge settlement messages.

### Architecture

See `architecture.tex` (directly authored LaTeX/TikZ) and the one-page
`architecture.pdf`. The innovation is a model/context/tools pipeline with
*hard* financial and cryptographic checks after every model decision.
`round3_policy.py` uses inventory, demand, product list prices, seller
reputation, historical purchases and payment confirmations.

### Execution contract (kit v4)

- `seller_r3.py`: A2A v1.0 Seller, signed receipts tied to actual units.
- `market_buyer.py --shops ... --item ... --budget ... --order ...`:
  ranks in-stock SKU offers and returns one A2A receipt + an ACP `accept`
  policy. The buyer's true budget is never advertised to the shop.
- `restock.py --state ...`: outputs `{"buy": [{"sku": "...","qty": 1}]}`.
  Bound each round of supply by available funds, max total stock and
  category demand. No bankruptcy request is automated.
- `review_hook.py --deal ...`: only executes after a genuinely settled
  trade and uses `settled` and `receipt` to generate a grounded review.
  Optionally sends a *factual*, rate-limited forum post to the kit's outbox.

### Quick bootstrap

A checked PowerShell bootstrap script is provided as
`setup_week08.ps1` (run it from a new PowerShell after stopping Week 07):

```powershell
powershell -ExecutionPolicy Bypass -File submissions/26510128/week-08/setup_week08.ps1
```

It pulls your Week 08 branch, merges the **official upstream v4 kit**,
copies your existing private keys locally, installs dependencies and runs
offline safety tests. The script stops on failures, and never prints keys.
After reviewing output, rerun with `-Join` to enter Round 3 and keep the
market/Cloudflare terminal running.

The `architecture.tex` is committed; the compiled `architecture.pdf`
(one A4 landscape page) must also be placed in this folder and committed. If unavailable, build
with `xelatex -interaction=nonstopmode -halt-on-error architecture.tex`
or download the supplied PDF in the ChatGPT conversation and place it
in the exact Week 08 directory.

### Setup on the Windows machine that owns keys

**The week-08 branch was prepared from the fork before syncing with the
latest upstream kit. Update the kit to v4 as below before live trading.**

1. Exit the prior market-kit session with Ctrl+C (the local keys remain).
2. Sync upstream course source into the fork's week-08 working copy:

```powershell
cd C:\Users\jangs\ai-agent-engineering-101
git fetch origin week-08
git switch week-08
git pull origin week-08
git fetch https://github.com/Q00/ai-agent-engineering-101.git main
git merge FETCH_HEAD
Select-String "KIT_VERSION" scripts\market_kit.py
```

The line must read `KIT_VERSION = "4"`. If git reports a merge conflict,
resolve it before starting or committing; don't overwrite Week 08 work.

3. Copy **only** the locally owned signing keys and preserved private memory
   from Week 07 into Week 08. Never transmit the keys to ChatGPT or GitHub:

```powershell
$src = "submissions\26510128\week-07"
$dst = "submissions\26510128\week-08"
Copy-Item "$src\shop_key.pem" "$dst\shop_key.pem"
Copy-Item "$src\buyer_key.pem" "$dst\buyer_key.pem"
New-Item -ItemType Directory -Force "$dst\.market" | Out-Null
if (Test-Path "$src\.market\buyer-memory.json") {
  Copy-Item "$src\.market\buyer-memory.json" "$dst\.market\buyer-memory.json"
}
```

Do not copy Week 07's 50+ MB Round 2 log as Week 08 proof. Kit v4 starts
`logs/market-kit.log` in this new directory. Keep the full original on
the live PC; do not commit private key files.

4. Install local dependencies and run offline tests:

```powershell
python -m pip install -r submissions/26510128/week-08/requirements.txt
cd submissions/26510128/week-08
python -m unittest test_round3_policy -v
cd ../../..
```

5. Start the local Ollama endpoint and the kit in a persistent terminal:

```powershell
$env:Path = "$env:LOCALAPPDATA\Programs\cloudflared;$env:Path"
$env:MARKET_OWNER = Read-Host "Current market claim code (keep private)"
python scripts/market_kit.py join --dir submissions/26510128/week-08 --market https://market.ourolabs.ai --name JangSeongWoong --tunnel
```

Check for `[kit] market_kit 4`, shop and buyer registration,
`[supply]`, seller completed A2A, `[acp] paid` or `[acp] settled`,
`verified True`, and **actual counted Round 3 settlements** on the official
dashboard. A2A completed receipts without ACP settlement do **not** count.

### Submission checklist (Round 3 ends 2026-11-17 23:59 KST; PR due 2026-11-23 23:59 KST)

- At least **20 settled shop deals and 20 settled buyer deals** in Round 3.
- Source and `agent.json`; `architecture.tex` and its compiled PDF.
- `REPORT.md`: model/context/tools, design originality, real settlement
  metrics, refusals, Players standings and a meaningful comparison.
- `logs/market-kit.log` (must include real Round 3 settlement entries).
- Open a PR titled `[week-08] 26510128` against official `main`
  **by 2026-11-23 23:59 KST**, and keep the kit, Ollama, PC and tunnel running until the Round 3 close (2026-11-17 23:59 KST).

Never submit `*.pem`, claim codes, `.env`, `.market/`, or secrets.
Check the staged file list before committing. Huge logs may require care.

### Honest evaluation note

No Round 3 settled counts or Player standings have been observed directly
in this development session. The implementation and architecture are
**prepared**, not a declaration of live success. Replace the pending
sections in `REPORT.md` only with real market-kit and board evidence.
