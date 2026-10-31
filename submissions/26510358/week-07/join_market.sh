#!/usr/bin/env bash
# Keep the registry identity stable; edit the seller's display name separately.
set -euo pipefail

if (( $# != 0 )); then
    echo "Usage: bash join_market.sh (registry name is fixed to myeonggi)" >&2
    exit 2
fi

agent_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$agent_dir/../../.." && pwd)"

for key in shop_key.pem buyer_key.pem; do
    if [[ ! -s "$agent_dir/$key" ]]; then
        echo "Missing $key; restore the existing key before rejoining." >&2
        exit 1
    fi
done

exec "$repo_dir/.venv/bin/python" -u "$repo_dir/scripts/market_kit.py" join \
    --dir "$agent_dir" \
    --market https://market.ourolabs.ai \
    --name myeonggi \
    --tunnel
