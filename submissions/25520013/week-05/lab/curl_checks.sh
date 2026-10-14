#!/usr/bin/env bash
# Hand-call the running HTTP server: a valid tools/list and tools/call, then the
# same requests with the routing header or the _meta envelope taken out.
# Start the server first:  uv run python tools_server.py --http
# Usage: bash curl_checks.sh 2>&1 | tee logs/curl-checks.txt
URL=${MCP_URL:-http://127.0.0.1:8000/mcp}
V=2026-07-28
META='"_meta": {"io.modelcontextprotocol/protocolVersion": "'$V'", "io.modelcontextprotocol/clientCapabilities": {}}'
META_NOCAPS='"_meta": {"io.modelcontextprotocol/protocolVersion": "'$V'"}'
BODY=$(mktemp)

call() {  # $1 label, $2 JSON body, $3... extra headers
  local label=$1 body=$2; shift 2
  local hdrs=() h shown=""
  for h in "$@"; do hdrs+=(-H "$h"); shown+=" -H '$h'"; done
  echo "== $label"
  echo "\$ curl -X POST $URL$shown -d '$body'"
  curl -s -o "$BODY" -w 'HTTP %{http_code}\n' -X POST "$URL" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H "MCP-Protocol-Version: $V" "${hdrs[@]}" -d "$body"
  cat "$BODY"; echo; echo
}

LIST='{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {'"$META"'}}'
CALC='{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "calculator", "arguments": {"expression": "48000+9500"}, '"$META"'}}'

call "1. tools/list (valid)" "$LIST" "Mcp-Method: tools/list"
call "2. tools/list without the Mcp-Method header" "$LIST"
call "3. tools/list without clientCapabilities in _meta" \
  '{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {'"$META_NOCAPS"'}}' "Mcp-Method: tools/list"
call "4. tools/call calculator (valid)" "$CALC" "Mcp-Method: tools/call" "Mcp-Name: calculator"
call "5. tools/call calculator without the Mcp-Name header" "$CALC" "Mcp-Method: tools/call"
rm -f "$BODY"
