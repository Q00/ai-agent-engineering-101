#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONDONTWRITEBYTECODE=1
private_env="${AX_LAB_ENV_FILE-${HOME}/.config/ax-agent/openai.env}"
if [[ -n "$private_env" ]]; then
  if [[ ! -f "$private_env" ]]; then
    printf '%s\n' 'Private API environment file not found. Export OPENAI_API_KEY and set AX_LAB_ENV_FILE="".' >&2
    exit 2
  fi
  exec env -u OPENAI_API_KEY uv run --frozen --python 3.12.11 --env-file "$private_env" run.py "$@"
fi
exec uv run --frozen --python 3.12.11 run.py "$@"

