#!/bin/sh
set -eu

# Prefer this worktree's verified environment, then the historical lab one.
worktree_python="../../../.venv/bin/python"
legacy_lab_python="../../../a2a-lab/.venv/bin/python"
if [ -x "$worktree_python" ]; then
    exec "$worktree_python" "$@"
fi
if [ -x "$legacy_lab_python" ]; then
    exec "$legacy_lab_python" "$@"
fi
exec python3 "$@"
