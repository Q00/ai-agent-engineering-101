# Week07 VPS seller (1.2.0)

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
  tunnel logs, immutable copied baseline, guard status and single-owner lock.
- `AX_WEEK07_MARKET_DIR` (absolute path, UID 10001 writable): persistent `.market`
  cache. The monitor resolves this relative to its source, despite the harness's
  temporary working directory.

The tunnel config must retain the existing tunnel ID, point its credentials-file
at `/run/private/<existing-credential>.json`, and send the existing hostname to
`http://127.0.0.1:8900` (with a final 404 ingress rule). Never run the same named
tunnel concurrently on Mac and VPS: seller negotiation state is process-local.
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

The guard stops both child process groups at **2026-10-21 09:00 Asia/Seoul** or
when the immutable baseline plus retained `seller.log` MODEL_USAGE records reaches
**5000**, whichever occurs first. Each MODEL_USAGE record counts one observed
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
baseline and child logs survive container rebuild/recreation. Restart loses live
in-memory negotiation sessions, as in the original seller.

## Local validation

No dependencies are installed and no model/provider request is sent:

```sh
VPS_TEST_EVIDENCE=/tmp/week07-guard-unique python3 test_guard.py
../../.venv/bin/python test_public.py
```

Use a fresh evidence directory for each guard run; existing evidence is preserved.
The public test uses the actual app and ASGI routes with an in-memory ephemeral
signer, replacing only the server/model boundary. It does not exercise real Docker,
Cloudflare, private-key provisioning or remote deployment. Those checks belong to
the cutover operator. Captured local results and exact invocations are indexed in
[the evidence record](.omo/evidence/RESULTS.md).
