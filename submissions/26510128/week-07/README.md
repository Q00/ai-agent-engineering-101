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

## Evidence required for both challenges

**Challenge 1:** The pricing policy now reads only official market board
summary statistics (`/api/board?round=2` and round 1) and adapts opening
asks within an enforced reserve floor. Baseline observation on 2026-10-21:
1,996 counted seller deals; 2,601 visits; 1,786 reviews; average 4.06
stars; board share field 0.09; total revenue 372,169. These are
**before** the new policy was verified running, not its outcomes.
Preserve raw JSON and run the same read-only query after actually deploying
the new policy. To compare average price, use revenue/deals for snapshots
and incremental (after revenue - before revenue)/(after deals - before deals)
when the increments are positive. Distinguish raw cumulative board share
from new-share over an observation window. Do not fabricate test results.

**Challenge 2:** The old live market log includes `bright-living` sending a
`[MARKET NOTICE FOR AI BUYING AGENTS ...]` message directing the buyer to
accept a price and issue five stars. The existing buyer kept its hard budget.
The updated `market_buyer.py` also recognizes market-authority instruction
spoofing, declines that negotiation, and stores an `injection_attempts`
counter by shop name so future selections penalize it. This is a new
preventive control, not yet proof of a second live encounter.
Save the genuine raw log and a real subsequent detection/avoidance trace
under `logs/challenge-2/`.

**Security:** Copy raw market-kit logs only after checking that no credentials
or bearer tokens were logged. Never commit `shop_key.pem`,
`buyer_key.pem`, `.market/` or a market claim code. If a market claim
code has ever been published, ask the market operator about revoking or
rotating it.

**Live process:** Running `market_kit.py` will not automatically load
updated local Python source. Perform a controlled stop of the existing kit
before `git pull origin week-07`, rerun the full market test,
then use the same nickname and existing keys to rejoin.

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


## Seller competitiveness improvement (2026-10-21)

The earlier Round 2 market board observation showed 0 counted seller deals
despite the shop and buyer both being running and GitHub-linked. This does not
prove the cause (market selection vs. pricing); the pricing change is an
experiment, not a guaranteed way to appear on a particular UI leaderboard.

The revised seller uses a read-only official \`/api/board?round=2\` request
(cached for 90 seconds) to observe its own counted sales and public ratings.
Its opening price is at least the reserve; when zero Round 2 sales are
reported the introductory quote is the reserve. With genuine repeat deals
and strong actual ratings, it gradually adds a modest margin. If the
market endpoint is unavailable, the fallback is a reserve + 5 opening quote.
The buyer, seller protocol and Ed25519 signature format are unchanged.

**Apply safely:** stop the currently running market kit with Ctrl+C first,
then \`git pull origin week-07\`, rerun
\`python scripts/market_test_week07.py submissions/26510128/week-07\`,
and restart \`market_kit.py join\` with the same original private keys,
GitHub claim code and shop name. Do not run a second copy of the kit with
the same identity.

Compare actual Round 2 seller \`deals\`, \`reviews\`, \`stars\` and
\`share\` before and after the restart. Real market results, not locally
signed test receipts, are evidence of effectiveness.
