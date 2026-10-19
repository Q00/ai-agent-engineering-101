# Public deployment readiness — 2026-10-19

Current status (2026-10-20): FREE PUBLIC CONFIG PREPARED; the owner subsequently
requested a free API for the public variant only. Cloudflare sign-in, a dedicated
AI credential and account Free-plan confirmation are pending. No public trade
success is claimed in this record. Earlier deferral below is historical.

## Observed checks

- Full offline suite: `python -m pytest -p no:cacheprovider -q` → **100 passed**.
- Ruff ALL: `ruff check .` → all checks passed before the two verifier regression
  cases; focused Ruff after their addition also passed.
- Strict basedpyright: `basedpyright --pythonpath <existing Python 3.12.11>` →
  **0 errors, 0 warnings, 0 notes** after the verifier fix.
- Official `scripts/check_week06.py submissions/25620027/week-06` → all checks
  passed, 41 source files parse and the existing current-kit **21/21** report
  remains valid. No paid basic-kit rerun was performed in this deployment task.
- Independent code review initially reproduced a verifier accepting receipt 120
  after an offer of 100. It was fixed by matching the receipt to the actual last
  offer. Both HTTP-level boundary cases independently passed; final review APPROVE.
- Docker build on the existing Linux VPS succeeded with Python 3.12.11 and the
  existing frozen, hash-checked script dependencies.
- Built image: `ax-week06-25620027:20261019`, image manifest-list digest
  `sha256:958bb3c381b087d46b48b16bbdbc92cf2a463a357f998aa4d5158fe14afdb937`.
- A `--network none` container imported `public_seller` and the existing seller:
  `Linux runtime imports PASS`.
- NGINX `nginx -t` passed. Its image and the official cloudflared 2026.10.1 image
  were pulled and their actual digests pinned in `compose.tunnel.yaml`.
- Dedicated Cloudflare tunnel and `ax-week06.treasuresky.xyz` DNS were created.
  An incorrectly suffixed, unused record was removed with an exact-name and
  exact-target check; the correct record was independently retained.

## Approval boundary

Automatic review rejected copying the existing OpenAI model environment file to
the VPS because the human had not explicitly authorized that specific secret
transfer. The copy did not execute. A combined approval request now covers only
the existing model environment and the new, dedicated tunnel credential, sent
over SSH into the service's private VPS directory. Neither file is in Git or the
build context. No alternate credential path was used to bypass the rejection.

The owner's 2026-10-20 reply did not authorize this transfer: credentials are to
remain unplaced, with activation considered only if requested later. There is no
pending automatic activation. The prepared runtime would cap model HTTP attempts
at 600 per UTC day; the previously proposed live scope was at most six virtual-
product negotiations. Neither paid runtime nor public verification was started.

## 2026-10-20 record reconciliation

The existing assignment note, academic current_state, academic source_manifest
and course RAW source_manifest were updated in place. Required assignment status
remains submitted with PR #293; optional public deployment is recorded as
prepared, keys unplaced, service activation deferred and public trades unverified.
No automatic activation or extra-credit completion is recorded.

The delegated recorder reread all four files and checked one new decision section
per note/manifest, the current AX entry, preserved submitted frontmatter, valid
nonempty local evidence links and the explicit note that `agent.json.deployed`
was not set. The publishing owner separately checked the actual agent.json file.
Its Python verification exited 0 and returned PASS for all four existing records.
No additional project ledger or source transcript was created or modified.

## 2026-10-20 free public configuration

- Original OpenAI seller/buyer CLI model defaults and agent.json are unchanged.
- Public-only configuration requires an explicit Cloudflare Workers AI endpoint,
  model and private token. Ambient custom headers are rejected before a client
  opens; ambient organization/project settings and redirects are excluded.
- Full offline suite: **117 passed**. Ruff ALL passed; source-only format check
  reports 49 formatted files; strict basedpyright reports 0 errors/warnings/notes.
  Raw commands and outputs are in ../free-public-model-config/EXECUTOR_REPORT.md.
- Independent review: CLEAR / APPROVE after reproducing and fixing the SDK's
  ambient Authorization-header override. Its 26 focused tests passed.
- Official checker exited 0: 43 source files parse, original current-kit 21 runs
  pass, required logs and REPORT are present. No new paid trade test was run.
- Read-only account lookup with existing tunnel authentication succeeded, but
  Workers AI model listing returned HTTP 403. The tunnel token was not reused
  as an inference credential. Chrome is waiting for the owner's Cloudflare login.
- Free plan and live GLM-4.7-Flash tool-calling remain unverified; agent.json has
  no deployed URL and optional extra credit remains incomplete.
- The existing assignment note, academic current_state, academic source_manifest
  and course RAW source_manifest now record the free-public-only decision and
  sign-in/key/plan verification still pending. The recorder's fresh read and
  assertions exited 0 with PASS 4/4; submitted assignment status was preserved.
