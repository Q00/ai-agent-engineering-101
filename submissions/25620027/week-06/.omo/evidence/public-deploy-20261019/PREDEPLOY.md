# Public deployment readiness — 2026-10-19

Current status (2026-10-20): PREPARED; owner explicitly deferred credential
placement and live activation. No public trade success is claimed in this record.

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
