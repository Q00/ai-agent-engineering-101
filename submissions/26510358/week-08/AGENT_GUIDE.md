# AGENT_GUIDE.md — for local agents (Claude Code, Codex, Grok, ...) improving our Round 3 shop and buyer

Read this first. You work on a clone; the SMC server runs the market 24/7 and deploys what you push.

## The loop
```
you: edit -> python submissions/26510358/week-08/ops/ci.py -> git commit (explicit paths) -> git push origin week-08
server (every 1-2 min): fetch hub -> CI -> deploy -> restart if needed -> watch 20 min -> auto-revert on trouble
you: git fetch origin 'refs/notes/*:refs/notes/*' && git notes --ref=ci show <sha>   # CI / DEPLOYED result
```
`origin` is the server hub (ssh://myeonggi@100.103.123.87/home/myeonggi/git/ai-agent-engineering-101.git).
A failing commit is marked bad and never deployed; fix it with a new commit.

## Rules (the hub hook and CI refuse violations)
- Write only inside `submissions/26510358/`. Never touch `scripts/`, `weeks/`, `roster/`, `.github/`, other students.
- Never commit keys, tokens, `.env`, `*.pem`. Never paste secrets into chat.
- Never force-push, squash or rewrite history. Never edit or tidy files under `logs/`.
- `git add <paths>` + `git commit -- <paths>`; never `git commit -a` (the server has unrelated modified logs).
- One change per commit. Start the message with your tag: `[grok] buyer: ...`, `[codex] shop: ...`.
- Ask the human before: changing pricing/threshold constants by more than ~20%, disabling a guard, anything
  that stops the shop or buyer, or any change to `ops/`.

## What wins (buyer leaderboard)
ALL = 0.5*saved_pct + 0.3*forum + 0.1*deals_pct + 0.1*posts_pct
- saved = sum(surplus/list) / (deals + 100). A broken good without warranty counts -1, food that spoiled -1.
- Measured 11-10: saved 0.296 (rank 22/61), forum 0.877 (rank 38), deals 14240 (rank 6), posts 1256 (rank 21).
- Biggest levers: (1) avoid break losses (C-grade goods break ~35%), (2) more high-discount deals,
  (3) one forum post every 5 min, (4) keep forum quality.

## Where the code is (submissions/26510358/week-08)
- Live market kit = official `scripts/market_kit.py` + our `kit_patches.py`. The week-07 code (local `market_kit.py`,
  `seller.py`, `buyer.py`, `market_buyer.py`, `harness.py` and their tests) was removed on 2026-11-10; it is in git history.
- `r3lib/score_model.py` = what an action is worth on the board (the unit of every reward, B and C);
  `r3lib/initiative.py` judge() = act / wait / skip on p > C/(B+C), logged to `logs/r3/initiative.jsonl`;
  `r3lib/bandit.py` = L1 policy (first-ask arm in the shop, clearance arm in `r3lib/markdown.py`), rows in
  `logs/r3/decisions.jsonl`; `r3lib/forge.py` + `tools/forged/` = verified tools for the ReAct agents;
  `python3 -m r3lib.measure baseline|compare` = before/after a deploy.
- `buyer_r3.py` buyer loop, `shop_r3.py` shop, `restock_r3.py`, `forum_r3.py`, `review_r3.py`, `market_runtime.py`.
- `r3lib/config.py` all tunables, e.g. `MIN_SURPLUS_RATIO=0.30`, `FORUM_MIN_GAP_MIN=20`, `LEVER2_POST_GAP_S=300`,
  `MY_NAMES`. CI lists every changed constant in its note.
- `ops/` deploy, CI, Supabase sync (server side; ask first). `tests/` pytest suite (CI runs all of it).

## Market data (Supabase MCP, read-only)
Schema `market`, refreshed every 2 min from the server. Start with `select * from market.readme;`
- `purchases`, `negotiations`, `our_settles`, `our_breaks`: our deals, haggling, outcomes, breakage.
- `restocks`, `stockouts`, `notes`, `our_forum`: shop stock events, notes, our forum posts.
- `kpi`, `buyer_decisions`, `decisions`, `forum_log`: JSONL logs as (line, ts, data jsonb).
- `board_snapshots`: leaderboard every 10 min — use it to measure the effect of a deploy.
Timestamps `ts` are unix seconds; KST: `to_timestamp(ts) at time zone 'Asia/Seoul'`.

## Before you push
```
python submissions/26510358/week-08/ops/ci.py          # needs a venv with requirements.txt + pytest + ruff
```
The server runs the same CI anyway; local CI just saves a round trip.
