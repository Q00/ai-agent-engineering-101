#!/usr/bin/env bash
# Week 07 — keep the market kit running on this machine until the deadline.
#
#   bash run_kit.sh                                     # foreground
#   nohup setsid bash run_kit.sh >/dev/null 2>&1 &      # detached: survives the terminal / IDE session
#
# Deployment (REPORT §6): shop + buyer "YS-SHOP" on this always-on NAS. The kit proxies the seller
# on a FIXED port; a cloudflared quick tunnel (http2 — QUIC is unreliable here) exposes that port.
# The tunnel is started by this script, not by the kit, because a fresh trycloudflare hostname
# takes 60-120 s to resolve from this network and the kit only waits 120 s: here we wait for DNS
# first, then hand the kit the URL with --public-url/--port, so its check passes at once. The
# name and the key stay the same across restarts, so trades and reviews carry over even when the
# URL changes. The claim code that links the agents to GitHub is read from ~/.market_owner
# (outside the repository) when that file exists. Logs: logs/market-kit.log (the kit),
# logs/run_kit.log (this loop), logs/tunnel.log (cloudflared).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$HERE"

export PATH="/opt/conda/bin:$HOME/.local/bin:$PATH"           # python 3.10 with a2a-sdk; cloudflared (http2 wrapper)
export CLAUDE_BIN="${CLAUDE_BIN:-$(ls -d "$HOME"/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | tail -1)}"
unset ANTHROPIC_API_KEY OPENAI_API_KEY OPENAI_BASE_URL         # the harness is claude -p on the subscription

MARKET="${MARKET_URL:-https://market.ourolabs.ai}"
NAME="${MARKET_NAME:-yeonsong2}"                              # the GitHub login, as the market's agent.md suggests
PORT="${KIT_PORT:-8900}"                                       # the kit's proxy port the tunnel forwards to
mkdir -p logs
note() { echo "$(date '+%F %T') [run_kit] $*" >> logs/run_kit.log; }

TUNNEL_PID=""; URL=""
start_tunnel() {
  : > logs/tunnel.log
  cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >> logs/tunnel.log 2>&1 &
  TUNNEL_PID=$!
  URL=""
  for _ in $(seq 1 60); do
    URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" logs/tunnel.log | head -1); [ -n "$URL" ] && break; sleep 1
  done
  [ -z "$URL" ] && { note "cloudflared printed no URL; retrying"; kill "$TUNNEL_PID" 2>/dev/null; return 1; }
  note "tunnel $URL -> 127.0.0.1:$PORT; waiting for DNS"
  for _ in $(seq 1 90); do                                     # DNS for a fresh hostname: usually 60-120 s here
    python - "$URL" <<'EOF' 2>/dev/null && { note "DNS resolves"; return 0; }
import socket, sys; socket.gethostbyname(sys.argv[1].split("//")[1])
EOF
    sleep 3
  done
  note "DNS never resolved for $URL; restarting the tunnel"; kill "$TUNNEL_PID" 2>/dev/null; return 1
}

while true; do
  # the market refuses joins without a claim code (401), so wait for ~/.market_owner before trying
  if [ ! -s "$HOME/.market_owner" ]; then
    [ -z "${WAITED:-}" ] && note "no ~/.market_owner yet (claim code from $MARKET/join); waiting before joining" && WAITED=1
    sleep 20; continue
  fi
  WAITED=""
  if [ -z "$TUNNEL_PID" ] || ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    start_tunnel || { sleep 20; continue; }
  fi
  export MARKET_OWNER="$(tr -d '[:space:]' < "$HOME/.market_owner")"
  note "starting market_kit join as $NAME via $URL (owner: ${MARKET_OWNER:+set}${MARKET_OWNER:-not set})"
  python "$REPO/scripts/market_kit.py" join --dir "$HERE" --market "$MARKET" --name "$NAME" \
    --port "$PORT" --public-url "$URL" ${MARKET_OWNER:+--owner "$MARKET_OWNER"} >> logs/run_kit.log 2>&1
  note "kit exited with $?; restarting in 30 s"
  sleep 30
done
