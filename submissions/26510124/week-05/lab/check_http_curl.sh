#!/usr/bin/env bash
# Run against tools_server.py --http; no model API is used.
set -euo pipefail
url="${1:-http://127.0.0.1:8000/mcp}"

for check in valid missing_method missing_capabilities; do
  headers=(-H 'Content-Type: application/json'
           -H 'Accept: application/json, text/event-stream'
           -H 'MCP-Protocol-Version: 2026-07-28')
  if [[ "$check" != missing_method ]]; then
    headers+=(-H 'Mcp-Method: tools/list')
  fi
  if [[ "$check" == missing_capabilities ]]; then
    body='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}}'
  else
    body='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
  fi
  response=$(curl --silent --show-error --max-time 10 "$url" \
    "${headers[@]}" --data "$body" --write-out '\nHTTP_STATUS:%{http_code}\n')
  printf '%s\n%s\n' "$check" "$response"
  expected=400
  if [[ "$check" == valid ]]; then expected=200; fi
  [[ "$response" == *"HTTP_STATUS:$expected" ]] || exit 1
done
printf 'PASS: curl tools/list returned 200; both missing-metadata requests returned 400\n'
