#!/bin/sh
# Step 6 of the lab: attach the same HTTP server to Claude Code as a second client.
# Start the server first:  python tools_server.py --http
# This is the command-line form of `claude mcp add --transport http week01-tools ...`,
# passed per run so nothing is written to the user's Claude Code settings.
#   --tools ""             no built-in Read/Bash: the file is reachable only through the server
#   --allowed-tools ...    lets the server's tools run without an approval prompt
# stream-json prints every tool_use and tool_result, which goes into the log.
GOAL=${1:-"Read notes.txt and sum the numbers in it."}
claude -p "$GOAL" \
  --model claude-haiku-4-5-20251001 \
  --tools "" \
  --strict-mcp-config \
  --mcp-config '{"mcpServers":{"week01-tools":{"type":"http","url":"http://127.0.0.1:8000/mcp"}}}' \
  --allowed-tools "mcp__week01-tools__*" \
  --exclude-dynamic-system-prompt-sections \
  --output-format stream-json --verbose < /dev/null |
python3 "$(dirname "$0")/stream_log.py"
