#!/usr/bin/env bash
# Host-side stop of the trading container and the tunnel; data and private files stay.
set -euo pipefail
BASE=/docker/ax-week08-25620027
docker compose -p ax-week08-25620027-r3 -f "$BASE/current/runtime/deploy/vps/compose.yml" --profile r3 down --timeout 20 || true
docker rm -f ax-week08-25620027-tunnel >/dev/null 2>&1 || true
docker ps -a --format "{{.Names}}\t{{.Status}}" | grep ax-week08 || echo "no ax-week08 containers"
