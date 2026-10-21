#!/bin/bash
set -eu
cd "$(dirname "$0")/../../../.."
runtime=/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin
evidence=.omo/evidence/release-1.3.0-20261021/persistence
"$runtime/python" -m pytest tests/test_seller_persistence.py -v -s \
  --basetemp "$evidence/sqlite-scenarios" \
  --junitxml "$evidence/pytest-final.xml" > "$evidence/pytest-final.log" 2>&1
"$runtime/ruff" check tests/test_seller_persistence.py > "$evidence/ruff-final.log" 2>&1
"$runtime/ruff" format --check tests/test_seller_persistence.py >> "$evidence/ruff-final.log" 2>&1
"$runtime/basedpyright" --pythonpath "$runtime/python" tests/test_seller_persistence.py \
  > "$evidence/types-final.log" 2>&1
"$runtime/python" /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.40/skills/programming/scripts/python/check-no-excuse-rules.py \
  tests/test_seller_persistence.py > "$evidence/skill-audit.log" 2>&1
shasum -a 256 tests/test_seller_persistence.py market_a2a/seller_store.py \
  market_a2a/seller.py market_a2a/seller_turn.py market_a2a/seller_context.py \
  > "$evidence/source-sha256.txt"
