#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
role="${1:-}"
case "$role" in
  shop) flags=(--shop-only --tunnel) ;;
  buyer) flags=(--buyer-only) ;;
  procurement|supervisor|watch) flags=() ;;
  *) echo "Usage: bash run_ubuntu.sh shop|buyer|procurement|supervisor [start|status|stop]|watch [shop|buyer|procurement|all]" >&2; exit 2 ;;
esac
for file in shop_key.pem buyer_key.pem .market/owner.env; do
  [[ -f "$HERE/$file" ]] || { echo "Restore private ZIP first: missing $file" >&2; exit 1; }
done
[[ -f "$ROOT/.venv/bin/activate" ]] || { echo "Create .venv at repository root first." >&2; exit 1; }
source "$ROOT/.venv/bin/activate"
set -a
source "$HERE/.market/owner.env"
set +a
cd "$ROOT"
if [[ "$role" == supervisor ]]; then
  exec python "$HERE/market_supervisor.py" "${2:-status}"
fi
if [[ "$role" == watch ]]; then
  exec python "$HERE/market_supervisor.py" watch "${2:-all}"
fi
if [[ "$role" == procurement ]]; then
  cd "$HERE"
  exec python procurement_campaign.py --state .market
fi
exec python "$HERE/market_runtime.py" join --dir "$HERE" --market https://market.ourolabs.ai --name 25512088 "${flags[@]}"
