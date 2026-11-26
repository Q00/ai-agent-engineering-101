#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-${TMPDIR:-/tmp}/ax-week04-runtime-$(id -u)}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/ax-week04-uv-cache-$(id -u)}"
export PYTHONDONTWRITEBYTECODE=1
private_env="${AX_LAB_ENV_FILE-${HOME}/.config/ax-agent/openai.env}"
if [[ -n "$private_env" ]]; then
  if [[ ! -f "$private_env" ]]; then
    printf '%s\n' 'Private API environment file not found. Export OPENAI_API_KEY and set AX_LAB_ENV_FILE="".' >&2
    exit 2
  fi
  exec env -u OPENAI_API_KEY -u OPENAI_BASE_URL -u AGENT_MODEL \
    uv run --frozen --python 3.12.11 --env-file "$private_env" python run_hybrid.py "$@"
fi
exec uv run --frozen --python 3.12.11 python run_hybrid.py "$@"
