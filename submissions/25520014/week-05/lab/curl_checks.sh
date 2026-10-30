#!/bin/sh
# Step 3 of the lab: call the HTTP server by hand. Start it first:
#   python tools_server.py --http
# Output is saved as logs/curl-checks.txt.
URL=http://127.0.0.1:8000/mcp
H='-H Content-Type:application/json -H Accept:application/json,text/event-stream -H MCP-Protocol-Version:2026-07-28'
FULL='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
NOCAPS='{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}}'
CALL='{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"calculator","arguments":{"expression":"48000+9500"},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'

echo "== (1) tools/list, all headers =="
curl -s -w '\nHTTP %{http_code}\n' $H -H Mcp-Method:tools/list -d "$FULL" $URL
echo; echo "== (2) tools/list, no Mcp-Method header =="
curl -s -w '\nHTTP %{http_code}\n' $H -d "$FULL" $URL
echo; echo "== (3) tools/list, no clientCapabilities in _meta =="
curl -s -w '\nHTTP %{http_code}\n' $H -H Mcp-Method:tools/list -d "$NOCAPS" $URL
echo; echo "== (4) tools/call calculator =="
curl -s -w '\nHTTP %{http_code}\n' $H -H Mcp-Method:tools/call -H Mcp-Name:calculator -d "$CALL" $URL
