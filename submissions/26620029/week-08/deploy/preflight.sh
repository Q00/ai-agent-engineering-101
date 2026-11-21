#!/bin/bash
# Checks inside the container with no trading: dependencies, secrets, the seller's card, market reachable, harness login.
# It never joins the market. Run: docker compose ... run --rm --no-deps --entrypoint bash kit .../deploy/preflight.sh
AGENT=/srv/market/repo/submissions/26620029/week-08
cd "$AGENT" || exit 1
export HOME=/tmp/kit; mkdir -p "$HOME"
ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; FAILED=1; }
python -V; claude --version; cloudflared --version | head -1
python -c "import a2a, httpx, uvicorn, starlette, cryptography" && ok imports || bad imports
for f in shop_key.pem buyer_key.pem market_owner.txt; do [ -s "$f" ] && ok "$f present" || bad "$f missing"; done
python - <<'EOF' && ok "keys load" || bad "keys load"
from pathlib import Path
from cryptography.hazmat.primitives import serialization
for f in ("shop_key.pem", "buyer_key.pem"):
    serialization.load_pem_private_key(Path(f).read_bytes(), password=None)
EOF
touch .market/.w logs/.w && rm .market/.w logs/.w && ok "state and logs writable" || bad "state and logs writable"
python -c "import json;a=json.load(open('agent.json'));assert 'python ' in a['seller']['cmd'] and 'D:' not in a['seller']['cmd']" && ok "agent.json is the Linux one" || bad "agent.json"
python seller_r3.py --port 18999 > /tmp/seller.log 2>&1 &
SP=$!; sleep 6
curl -fs http://127.0.0.1:18999/.well-known/agent-card.json | python -c "import json,sys;print('card name',json.load(sys.stdin)['name'])" && ok "seller card" || { bad "seller card"; tail -5 /tmp/seller.log; }
kill $SP 2>/dev/null
curl -fs -o /dev/null https://market.ourolabs.ai/api/board && ok "market reachable" || bad "market reachable"
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  A=$(cd /tmp && echo "Reply with only the number 42" | claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence 2>&1)
  [ "$A" = "42" ] && ok "harness answered 42" || bad "harness: ${A:0:120}"
else
  echo "wait harness: no CLAUDE_CODE_OAUTH_TOKEN yet"
fi
[ -e /srv/market/control/STOPPED ] && ok "STOPPED flag set (no trading)" || echo "note STOPPED flag not set"
exit ${FAILED:-0}
