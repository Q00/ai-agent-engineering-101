#!/usr/bin/env bash
# The six required runs: prompt_inject and server_inject, three repeats each. One log per run.
#
# Keys only from the environment; none is written here or in any .py.
#   export OPENAI_API_KEY=...          # your OpenAI key
#   unset OPENAI_BASE_URL              # OpenAI directly
#   export AGENT_MODEL=gpt-4o-mini AGENT_TEMPERATURE=0
#   export PY=python3                  # interpreter with requirements.txt installed
#
# The market is started here with a fresh admin token (never committed) and stopped at the end.
# Rerunning skips (run, condition, scenario) rows already in results.csv; logs are appended (tee -a).
set -u
cd "$(dirname "$0")"
PY=${PY:-python3}
mkdir -p logs server_logs
export MARKET_ADMIN_TOKEN=${MARKET_ADMIN_TOKEN:-$($PY -c "import secrets; print(secrets.token_hex(16))")}
export PORT=${PORT:-8001} MARKET_URL=${MARKET_URL:-http://127.0.0.1:${PORT:-8001}}

$PY market_server.py >> server_logs/market.txt 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 4

$PY auth_checks.py

for condition in prompt_inject server_inject; do
  for repeat in 1 2 3; do
    run=$(printf "%s-%02d" "$condition" "$repeat")
    echo "=== $run ==="
    $PY runner.py --condition "$condition" --repeat "$repeat" 2>&1 | tee -a "logs/$run.txt"
  done
done

$PY summarize.py
