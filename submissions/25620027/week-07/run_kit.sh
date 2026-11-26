#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$source_dir/../../.." && pwd)"
if [[ ! -x "$source_dir/.venv/bin/python" ]]; then
  printf '%s\n' 'Week07 environment is missing; follow SETUP.md first.' >&2
  exit 1
fi
exec "$source_dir/.venv/bin/python" "$repo_root/scripts/market_kit.py" "$@"
