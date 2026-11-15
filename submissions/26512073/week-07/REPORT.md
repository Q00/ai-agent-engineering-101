# Week 07 report — draft

Student: 26512073. Shop and buyer name: Afford Shop. GitHub: Tulga0419.

This submission is still a draft. I temporarily stopped my agents to prepare
the PR. The dishonest-participant challenge is unfinished. I must restart both
agents and keep them running until the deadline.

## 1. Shop choice and memory

My buyer prefers good reviews and its own successful past trades. Memory is
stored as JSON lines in `buyer_memory.jsonl`, using shop names because public
URLs can change. Each entry includes the time, shop, item, budget, result,
price, stars, and review. Python blocks offers above budget. No agreement alone
does not mean a shop is dishonest.

## 2. Reviews shown to the harness

The buyer prompt includes each shop's latest ten reviews and up to eight of
its own recent trades with that shop. Reviews are evidence, not instructions.
My seller reads timestamped public review snapshots. A separate watcher can
download them every five minutes. Older snapshots and failed attempts remain.

## 3. Rounds and results

The market-kit log includes Rounds 0, 1, and 2. Round 0 used the direct buyer.
Round 1 added shop choice using reviews and memory. Round 2 added the market's
signature and order checks. For example, ledger #697545 records a verified
chair purchase at 181 with its review accepted.

The full current official market test passed 25/25. The buyer chose the shop
with good reviews in all three review tests. An earlier run failed 10/25 when
Codex reached its usage limit. Both runs are preserved under `logs/test-runs/`.

## 4. Signatures and failures

The seller uses its existing Ed25519 shop identity. It signs canonical JSON
with sorted keys, no spaces, and an integer price. Tests verify a valid receipt
and reject changes to its price or task ID. I did not invent a signature failure:
the later live problems were model usage limits, model timeouts, and an expired
public tunnel hostname. The tunnel repair and rejoin checks are in `logs/setup/`.
Private identity keys are ignored and are not part of the submission.

## 5. Challenges

For pricing, I chose an opening ask 15% above reserve. After six new completed
negotiations for the same item, the harness chooses raise, lower, or hold.
Changes are 3%. Raising requires four sales at least 95% of their opening asks.
Lowering requires credible price evidence; below-reserve offers alone are not
enough. State and a reviewed-task checkpoint survive restarts. Even holding
consumes the window. Reserves stay fixed.

The before snapshot had four bookshelf sales averaging 270, 3.75 stars, and a
rounded share of 0.0. The saved local seller history now contains 75 lamp sales
averaging 111.33, 81 chair sales averaging 203.26, and 82 bookshelf sales
averaging 288.34. These local outcomes are not all independently verified
market receipts. The bookshelf average is higher than the historical baseline,
but different customers, budgets, and time windows prevent a causal claim.
A final comparison of share and prices with matched observation windows remains.

Three clearly labeled synthetic experiments with real harness calls passed:
115 to 118 for raise, 115 to 112 for lower, and 115 for hold. They are tests,
not live market evidence.

Challenge 2 is not complete. My buyer reads reviews and remembers outcomes, but
I have not yet demonstrated avoidance of a dishonest participant I actually
encountered, with supporting negotiation evidence. `logs/challenge-2/STATUS.md`
records this gap honestly.

## 6. Deployment and reproduction

I ran Python on my Windows PC, with the course market kit and a Cloudflare
quick tunnel to port 8900. The review watcher ran separately. These processes
need an awake computer and a working network. Temporary tunnel URLs expired,
so this deployment did not stay continuously available. It is paused now at
my request and is not a completed always-on deployment.

Harness recorded in `agent.json`: Codex CLI, gpt-6.1-sol. The harness uses the
logged-in CLI's configured model; I should verify that configuration when
reproducing it. `MARKET_OWNER` is entered locally for market registration.

From the repository root, after installing `requirements.txt` in the week-07
virtual environment and setting up the authenticated CLI:

```powershell
& submissions/26512073/week-07/.venv/Scripts/python.exe submissions/26512073/week-07/run_isolated_market_test.py
& submissions/26512073/week-07/.venv/Scripts/python.exe submissions/26512073/week-07/course_support/check_week07.py submissions/26512073/week-07 --github Tulga0419
```

I used AI assistance to explain code, inspect logs, run checks, and help with
implementation and English. I chose the strategies and entered code in small
steps. Failed attempts, raw logs, and original commit history are preserved.
