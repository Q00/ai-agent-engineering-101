#!/bin/sh
# Step 6 of the lab, the way the lecture writes it: register the server with
# `claude mcp add`, ask the question, then unregister. Start the server first:
#   python tools_server.py --http
# Unlike second_client.sh (per-run --mcp-config), this goes through Claude Code's
# own registry (local scope, ~/.claude.json for this directory).
#   ENABLE_CLAUDEAI_MCP_SERVERS=false  keeps the account's claude.ai connectors
#                                      (Gmail, Slack, ...) out of the model's tool list
#   --tools ""                         no built-in Read/Bash
# The [host] line prints which servers and tools the model actually saw.
GOAL=${1:-"Read notes.txt and sum the numbers in it."}
cd "$(dirname "$0")"
claude mcp add --transport http week01-tools http://127.0.0.1:8000/mcp > /dev/null 2>&1
trap 'claude mcp remove week01-tools > /dev/null 2>&1' EXIT
ENABLE_CLAUDEAI_MCP_SERVERS=false claude -p "$GOAL" \
  --model claude-haiku-4-5-20251001 \
  --tools "" \
  --allowed-tools "mcp__week01-tools__*" \
  --exclude-dynamic-system-prompt-sections \
  --output-format stream-json --verbose < /dev/null |
python3 stream_log.py
