#!/usr/bin/env bash
# Render start command: the shop and the market kit (which runs the buyer) in one web service.
#
#   Root Directory : (repository root)
#   Build Command  : pip install -r submissions/26622007/week-07/requirements.txt
#   Start Command  : bash submissions/26622007/week-07/render_start.sh
#
# Environment (Render dashboard, never in the repository):
#   OPENROUTER_API_KEY  the shop's and the buyer's harness (Luna)
#   MARKET_OWNER        claim code, links the agents to the GitHub account
#   SHOP_KEY_PEM        contents of shop_key.pem  (same key as on the laptop: the market knows nocha12 by it)
#   BUYER_KEY_PEM       contents of buyer_key.pem
#   MARKET_NAME         optional, default nocha12
# Render sets PORT and RENDER_EXTERNAL_URL itself.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
NAME="${MARKET_NAME:-nocha12}"
URL="${RENDER_EXTERNAL_URL:?RENDER_EXTERNAL_URL is not set (run this on Render)}"
PORT="${PORT:-10000}"

cd "$HERE"
umask 077
for k in SHOP BUYER; do
  var="${k}_KEY_PEM"; file="$(echo "$k" | tr 'A-Z' 'a-z')_key.pem"
  if [ -n "${!var:-}" ]; then printf '%s\n' "${!var}" | sed 's/\\n/\n/g' > "$file"; fi
  [ -s "$file" ] || { echo "$var is missing: the market would see a new key and refuse the name $NAME" >&2; exit 1; }
done

export HARNESS=openrouter SHOP_MARKET_NAME="$NAME"
python seller.py --host 0.0.0.0 --port "$PORT" --public-url "$URL" &
SELLER=$!
python - "$PORT" <<'EOF'
import sys, time, urllib.request
for _ in range(120):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/.well-known/agent-card.json", timeout=2)
        sys.exit(0)
    except Exception:
        time.sleep(1)
sys.exit("seller did not start")
EOF

cd "$ROOT"
while kill -0 "$SELLER" 2>/dev/null; do
  python scripts/market_kit.py join --dir "$HERE" --market https://market.ourolabs.ai --name "$NAME" --deployed "$URL" || true
  echo "$(date '+%H:%M:%S') [render_start] kit exited, joining again in 10 s"
  sleep 10
done
echo "seller exited" >&2
exit 1
