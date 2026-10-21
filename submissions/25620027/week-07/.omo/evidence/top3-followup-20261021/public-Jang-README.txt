# Week 07: local Ollama A2A market agents

This is the Week 06 seller and buyer adapted for the Week 07 course market.

## Tested locally

The student's Week 07 kit output on 2026-10-20 showed **25/25 PASS**:
Ed25519 signed receipts, receipt-aware market buyer, review selection,
budget and A2A JSON-RPC v1.0 protocol.

## Windows setup from repository root

```powershell
python -m pip install -r submissions/26510128/week-07/requirements.txt
Invoke-RestMethod http://localhost:11434/api/tags
python scripts/market_test_week07.py submissions/26510128/week-07
```

`pricing_policy.py` was added **after** that successful test. Re-run
the test against the current branch before final submission.

## Market registration

1. Open https://market.ourolabs.ai/join and sign in with GitHub; get
   your **private** claim code; never upload or paste it to a chat.
2. Install Cloudflare tunnel:
   `winget install --id Cloudflare.cloudflared`
3. In PowerShell, privately enter the code:
   `$env:MARKET_OWNER = Read-Host 'Market claim code'`
4. Start the kit **from repository root**:
```powershell
python scripts/market_kit.py join --dir submissions/26510128/week-07 --market https://market.ourolabs.ai --name JangSeongWoong --tunnel
```
5. Keep the computer, Ollama and terminal on. For unattended persistent
   operation, use an always-on machine/VPS; a laptop may not stay available.
6. Confirm BOTH '[market] shop registered' and '[market] buyer registered'
   and look for a Round 2 line like 'verified True, review accepted'.
7. Run `python scripts/check_week07.py submissions/26510128/week-07`
   only after the live market has generated its `logs/market-kit.log`.

The kit creates `shop_key.pem` and `buyer_key.pem` at runtime; never
commit them. The local market-test already created a seller key, which
must be preserved for later joining.

## Challenge 1 input

Actual sales are appended locally in `.market/seller-sales.jsonl`.
If you have genuine market reviews, export them into
`.market/seller-reviews.json` as:
```json
{"shop":"JangSeongWoong","reviews":[{"stars":4,"review":"Actual review text","round":2}]}
```
Do not fabricate reviews to claim challenge completion.

## Challenge 2 input

For a real dishonest participant, preserve the original
`logs/market-kit.log` record and review evidence. Our buyer already
penalizes bad reviews and mismatched receipts, but the challenge needs a
real example in a live market.

## Before opening PR

```powershell
python scripts/market_test_week07.py submissions/26510128/week-07
python scripts/check_week07.py submissions/26510128/week-07
git status
git add submissions/26510128/week-07
git status --short
```

Review staged files before commit and make sure no `*.pem` or other
secrets appear.
