#!/usr/bin/env bash
# Host-side start (or restart) of a verified Round 3 release behind the named Cloudflare tunnel.
# Usage: launch-r3.sh <release-dir-name>   (release = releases/<name> with evidence/result.json)
set -euo pipefail
BASE=/docker/ax-week08-25620027
REL="$BASE/releases/${1:?release dir name}"
IMAGE="ax-week08-25620027:$(echo "$1" | cut -c1-7)-prepared"
test -f "$REL/evidence/result.json" || { echo "release not verified"; exit 1; }
docker image inspect "$IMAGE" >/dev/null
docker tag "$IMAGE" ax-week08-25620027:prepared
ln -sfn "$REL" "$BASE/current"
# Tunnel: host network so it reaches the kit proxy on 127.0.0.1:18088; runs as the
# private-file owner so it can read the 0600 config and credentials.
docker rm -f ax-week08-25620027-tunnel >/dev/null 2>&1 || true
docker run -d --name ax-week08-25620027-tunnel --network host --restart unless-stopped \
  --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 128m --pids-limit 64 --log-opt max-size=5m --log-opt max-file=2 \
  -v "$BASE/private/cloudflared.yml:/etc/cloudflared/config.yml:ro" \
  -v "$BASE/private/cloudflared.json:/etc/cloudflared/creds.json:ro" \
  cloudflare/cloudflared:2026.10.1 tunnel --no-autoupdate --config /etc/cloudflared/config.yml run >/dev/null
# Trading process: the release's compose file with the verified image, no rebuild.
export AX_R3_BUNDLE="$REL/runtime" AX_R3_PRIVATE_DIR="$BASE/private" AX_R3_DATA_DIR="$BASE/data" \
       AX_R3_ACTIVATE=1 AX_PAID_FALLBACK=anthropic
docker compose -p ax-week08-25620027-r3 -f "$REL/runtime/deploy/vps/compose.yml" --profile r3 up -d --no-build
docker ps --format "{{.Names}}\t{{.Status}}" | grep ax-week08
