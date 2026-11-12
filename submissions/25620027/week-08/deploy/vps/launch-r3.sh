#!/usr/bin/env bash
# Host-side start (or restart) of a verified Round 3 release behind the named Cloudflare tunnel.
# Usage: launch-r3.sh <release-dir-name>   (release = releases/<name> with evidence/result.json)
set -euo pipefail
BASE=/docker/ax-week08-25620027
REL="$BASE/releases/${1:?release dir name}"
IMAGE="ax-week08-25620027:$(echo "$1" | cut -c1-7)-prepared"
test -f "$REL/evidence/result.json" || { echo "release not verified"; exit 1; }
# The host's daily `docker image prune -af --filter until=24h` drops extra tags of
# an image in use (seen 2026-11-02: the release tag vanished, only `prepared` was
# left), which made this script stop before compose. Re-tag from the image id the
# release recorded when it was verified.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  ID=$(sed -n 's/.*"image_id": "\(sha256:[0-9a-f]*\)".*/\1/p' "$REL/evidence/result.json")
  [ -n "$ID" ] && docker image inspect "$ID" >/dev/null 2>&1 && docker tag "$ID" "$IMAGE"
fi
docker image inspect "$IMAGE" >/dev/null
docker tag "$IMAGE" ax-week08-25620027:prepared
ln -sfn "$REL" "$BASE/current"
# Tunnel: host network so it reaches the kit proxy on 127.0.0.1:18088; runs as the
# private-file owner so it can read the 0600 config and credentials. A running tunnel
# is kept: recreating it drops the edge connection for a while, and the kit's market
# join then times out and exits (seen 2026-11-01 09:00 KST).
if ! docker ps --format '{{.Names}}' | grep -qx ax-week08-25620027-tunnel; then
  docker rm -f ax-week08-25620027-tunnel >/dev/null 2>&1 || true
  docker run -d --name ax-week08-25620027-tunnel --network host --restart unless-stopped \
    --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --memory 128m --pids-limit 64 --log-opt max-size=5m --log-opt max-file=2 \
    -v "$BASE/private/cloudflared.yml:/etc/cloudflared/config.yml:ro" \
    -v "$BASE/private/cloudflared.json:/etc/cloudflared/creds.json:ro" \
    cloudflare/cloudflared:2026.10.1 tunnel --no-autoupdate --config /etc/cloudflared/config.yml run >/dev/null
  sleep 20   # let the tunnel register at the edge before the kit asks the market to fetch our card
fi
# Trading process: the release's compose file with the verified image, no rebuild.
export AX_R3_BUNDLE="$REL/runtime" AX_R3_PRIVATE_DIR="$BASE/private" AX_R3_DATA_DIR="$BASE/data" \
       AX_R3_ACTIVATE=1 AX_PAID_FALLBACK=anthropic
docker compose -p ax-week08-25620027-r3 -f "$REL/runtime/deploy/vps/compose.yml" --profile r3 up -d --no-build
# Every launch is meant to stay up: the health cron relaunches a container that
# exits (e.g. the kit's market join racing the fresh tunnel) while this marker exists.
touch "$BASE/r3-active"
docker ps --format "{{.Names}}\t{{.Status}}" | grep ax-week08
