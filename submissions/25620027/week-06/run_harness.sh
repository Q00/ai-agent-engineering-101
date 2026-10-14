#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
private_env="${AX_LAB_ENV_FILE-${HOME}/.config/ax-agent/openai.env}"
uv_args=(run --frozen --python 3.12.11 --script)
if [[ -n "$private_env" && -f "$private_env" ]]; then
  uv_args+=(--env-file "$private_env")
fi
exec uv "${uv_args[@]}" "$source_dir/harness_app.py" "$@"
