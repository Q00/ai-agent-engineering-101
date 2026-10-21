# Week07 VPS seller (1.3.1)

This package runs the existing seller and price comparison monitor, with a named
Cloudflare tunnel in the same container. `serve_public.py` uses the existing
`create_app`, Harness, Catalog and ReceiptSigner, advertising
`https://ax-week07.treasuresky.xyz/` and binding only `127.0.0.1:8900`.
It starts no market kit or autonomous buyer, and never registers the shop again.
The comparison monitor retains its existing bounded peer-price probes.

## Private inputs and persistent directories

Provision outside the source/build context, with directories readable by UID/GID
10001 and private files readable only by their owner:

- `AX_WEEK07_PRIVATE_DIR` (absolute path, read-only mount): `openai.env` containing
  the existing model credentials; the original `shop_key.pem`; `cloudflared.yml`
  and its referenced existing tunnel credential JSON; `baseline.json` containing
  `{"model_usage_count": N}` where N is the final cumulative local MODEL_USAGE
  record count after stopping local model activity at cutover.
- `AX_WEEK07_DATA_DIR` (absolute path, UID 10001 writable): permanent seller and
  tunnel logs, immutable copied baseline, guard status, single-owner lock and
  `seller-state.sqlite3` with its SQLite sidecar files.
- `AX_WEEK07_MARKET_DIR` (absolute path, UID 10001 writable): persistent `.market`
  cache. The monitor resolves this relative to its source, despite the harness's
  temporary working directory.

The tunnel config must retain the existing tunnel ID, point its credentials-file
at `/run/private/<existing-credential>.json`, and send the existing hostname to
`http://127.0.0.1:8900` (with a final 404 ingress rule). Never run the same named
tunnel concurrently on Mac and VPS: this deployment supports one seller process
and one persistent database, not concurrent workers with shared in-memory caches.
The signing key is copied into `/run/runtime` tmpfs (0600) so the existing loader
can chmod it without modifying the mounted original.

## Build, preflight and cutover

Run from this directory with the three absolute directory variables exported:

```sh
docker compose -f compose.yml config --quiet
docker compose -f compose.yml build
AX_TUNNEL_ENABLED=0 docker compose -f compose.yml up -d
```

Use dedicated staging DATA and MARKET directories and a staging baseline for
that preflight. Tunnel disablement does **not** disable the existing comparison
monitor; a running seller still has normal monitoring behavior. Inspect the
card/health and signing identity without issuing model requests. Stop staging,
stop the Mac seller/monitor, freeze its final baseline, then select separate new
production DATA/MARKET directories and provision the production baseline before:

```sh
AX_TUNNEL_ENABLED=1 docker compose -f compose.yml up -d --force-recreate
docker compose -f compose.yml ps
```

No host ports are published. The container has 1 CPU, 768 MiB, 128 PIDs, no Linux
capabilities, and UID 10001. Dependency installation occurs only during image
build: uv exports the existing script lock with `--frozen` and installs pinned
requirements with their hashes; runtime uv uses offline/no-sync mode.
The Dockerfile-specific allowlist excludes keys, env files, local logs and caches
from the build context. No secrets enter the image layers.

## Fixed stop contract

The user extended this run from 09:00 to 21:00 on 2026-10-21, then authorized
5000 additional observed calls (10000 cumulative) at the 13:48 deployment.
The same immutable baseline and cumulative logs are retained. The guard stops both child
process groups at **2026-10-21 21:00 Asia/Seoul** or
when the immutable baseline plus retained `seller.log` MODEL_USAGE records reaches
**10000**, whichever occurs first. Each MODEL_USAGE record counts one observed
completed model response, not tokens. The counter is observed from the model's
existing logs; concurrent requests already in flight can finish during shutdown,
so it is not a provider-side hard billing limit. All final child output is kept.

Deadline/cap and intentional SIGTERM produce exit 0; `on-failure:3` only retries
unexpected failures. A later manual start still checks the same absolute deadline
and cumulative log before starting either child. Never truncate/delete seller.log,
change the baseline, or replace the production DATA directory during this run.
A changed input baseline or missing saved baseline beside old logs fails closed.
Each usage chunk also persists an atomic count floor; a missing/truncated log
that would lower this count prevents child startup.
Child logs are append-only and fsynced; Docker log rotation does not affect usage
accounting. `guard-state.json` records the latest reason/count, while the original
baseline and child logs survive container rebuild/recreation.

## Negotiation recovery and release behavior

The wrapper defaults `AX_SELLER_STATE_DB` to `/data/seller-state.sqlite3`.
The file is owner-only (0600). SDK tasks, owner-scoped task/context identities,
negotiation summaries and terminal tombstones persist on the existing DATA mount.
Quote state is saved before publication; terminal state is closed before receipt,
rejection or cancellation publication. Reusing the same directory and signing key
allows later 1.3.0 app instances to continue an open task and reject terminal replay.
Malformed or commercially invalid restored state fails closed.

The initial upgrade cannot recover tasks held only in the old 1.2.1 process's
memory. This is not a zero-downtime migration or exactly-once receipt delivery
guarantee: a crash after terminal journaling but before delivery may leave the
buyer without a completed receipt. Do not delete the database to work around a
startup error.

An opening message may explicitly offer a basic SKU with `offer lamp 100` or
`buy chair for 180`; the whole-message grammar is strict. Budgets, dimensions,
fractional prices and trailing instructions are not interpreted as actual offers.
The model still selects the SKU/action, while code enforces the actual offer,
reserve and signature contract. In 1.3.1, a feasible explicit opening offer must
be accepted after product retrieval and price validation; a redundant quote is
rejected for model correction. Once a SKU is selected, follow-up model turns
use decision tools without repeating catalog search. A quote at the reserve
states that no further coupon reduction is available.

The public comparison highlights only observed first asks for up to five other
active shops, distinguishing sole and tied minima. Collection remains 600 seconds
with 900-second expiry; browser polling remains 60 seconds. Stale, failed or
unverified observations suppress lowest-price claims. Actual sale prices, equal
quality, global cheapest status and advertising lift are not established.

The packaged buyer's public rating cap is 3.5, represented as integer 3 for both
ordinary and good completed trades because the market permits integers only.
No deal alone remains 3; directly observed operator-directive incidents retain 1.
Its selection input omits numeric public stars but retains factual issue signals
and own trade facts. This service does not start that buyer or post those reviews.

## Local validation

No dependencies are installed and no model/provider request is sent:

```sh
VPS_TEST_EVIDENCE=/tmp/week07-guard-unique python3 test_guard.py
../../.venv/bin/python test_public.py
```

Use a fresh evidence directory for each guard run; existing evidence is preserved.
The public test uses the actual app and ASGI routes with an ephemeral signer and
a temporary SQLite file, replacing only the server/model boundary. It does not exercise real Docker,
Cloudflare, private-key provisioning or remote deployment. Those checks belong to
the cutover operator. Captured local results and exact invocations are indexed in
[the evidence record](.omo/evidence/RESULTS.md).

Release 1.3.0 and the 1.3.1 follow-up are indexed in [REPORT section 18](../../REPORT.md#18-130-협상-복구공개-별점-상한관측-가격-광고).
The local recovery suite uses fresh app instances and real SQLite/A2A routes;
remote container and public-route observations must be recorded separately there.
