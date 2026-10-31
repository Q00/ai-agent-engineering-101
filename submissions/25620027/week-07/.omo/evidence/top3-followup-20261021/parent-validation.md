# Parent validation and rollout — 2026-10-21 (Codex)

## Acceptance and actual surfaces

- Source commit `4bfb00a`: conservative pre-limit retrieval, basic-request candidate
  reduction and explicit quote bounds. Model remains `gpt-4.1-nano-2025-04-14` and
  still makes search and action decisions. Signed receipt and price guards remain.
- Executor's full offline suite: **178 passed** (167 existing +11 new). Final Ruff
  and strict basedpyright0errors/0warnings are `implementation/ruff-pass.txt` and
  `types-pass.txt`, indexed in checks.json. Earlier `types-final.txt` is an actual
  failed attempt, preserved rather than presented as passing.
- Independent review: APPROVE, zero blockers, one nonblocking finding that the
  card test pins prose/version. Review did not independently rerun tests:
  `../top3-followup-20261021-code-review.md`.
- New official real-model candidate run: **25/25 PASS**, external comparison OFF
  (`AX_PRICE_COMPARISON=0`). All actual test commands, logs, kit SHA and results are
  in `official-attempt-1/`. It observed83 completed model responses, input75894 /
  output3473 tokens. These local validation calls are separate from the persistent
  live-market guard counter, as previous official checks were. The original buyer
  memory was preserved before/after privately and restored after the run; no live
  automatic buyer was started. Previous tracked raw logs were preserved from HEAD
  before committing current test output. Test/console logs were not edited.
- Current structural checker: all checks passed, `structural-check.log`.
- Updated deadline guard: **11 offline tests passed**, `guard-21h-tests.log` and
  unique `guard-21h/` fixture artifacts. Source/guard21:00 commit `41f3e98`.
- Canary source changed only expected current version/evidence destination;
  Ruff and strict types pass (`canary-lint.log`, `canary-types.log`).

## VPS rollout and public negotiation

Build and deploy target only `/docker/ax-week07-25620027` on the approved Hermes
VPS. Archive is an explicit source allowlist, excluding keys/env/private directories
and local logs. `source-archive-manifest.txt` and SHA record the transfer. Harmless
macOS xattr header warnings are retained in `build-operation.log`.

Image **ax-week07-25620027-seller:1.2.1**, image ID
`sha256:98aae1edf9bcf46283215a1f380e947111a634492e6ac7c73d97392e4c060cbd`.
The single production container was recreated at **09:08:33 KST**; live in-memory
negotiations may be lost, so this is not a zero-downtime deployment. It retains
the original signing identity, public URL and persistent data/baseline. It does
not register again or start an automatic buyer.

At **09:08:58 KST**, `public-smoke.json`/`public-smoke-1.log` show actual public
card1.2.1 and lamp opening100 → offer1/counter100 → offer100/receipt100.
Ed25519 verifies against the market's registered public key. Result/review were
not submitted to the market and are excluded from market performance.
`server-seller-through-smoke.log` records search→decide and **one candidate** for
the strict basic request. Opening input was904+1087=1991 tokens across two model
calls. This is a single observation, not a controlled latency/efficiency test;
after the low offer the model still did an unnecessary additional search, so
elimination of all redundant calls is not claimed.

Post-canary `runtime-status.log` shows running/healthy, same baseline4354 and
cumulative count4433, absolute deadline **2026-10-21T21:00:00+09:00**, limit5000.
Earlier rollout status showing reason=signal was the old container's retained
shutdown state; subsequent running state confirms startup did not reset the count.
Hermes container ID/start/restart0 remain the same. Week06 containers remain up
(seller healthy); no new Week06 trade was performed.

The user's extension is implemented; the first of the21:00 deadline and observed
5000 cap ends this run. In-flight responses can still log during shutdown. There
is no promise that remaining responses last until21:00, and no provider-side
hard billing limit. Source archive is removed after retained manifest/hash.

## Current board and unresolved measurement

`market-snapshot.json` is the original08:53:57 board/registry evidence; the later
raw GET `market-board-after-rollout.json` was observed at **09:11:04 KST**.
The later board still caches our card1.2.0/old300-character description, although
the actual public A2A card is1.2.1. This patch does not claim that marketplace
registration metadata refreshed. No extra rejoin was performed. The default kit
shop list only carries name/URL/reviews; copy improvements cannot guarantee new
preselection traffic.

Ratings, visits, item mix, versions and time exposure differ. No immediate
ranking, conversion, profit or availability improvement is established. The
top-three live exact model IDs and continuous24-hour uptime remain unverified
except the explicitly documented public defaults. Required challenge effect
analysis, server reboot exercise and Week07 PR submission remain separate work.

Raw Docker logs have trailing spaces. They are retained unchanged; whitespace
checks target source/document changes rather than rewriting raw evidence.
