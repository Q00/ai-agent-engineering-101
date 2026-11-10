#!/bin/bash
# 20분마다 시장 관찰 스냅샷 저장 (그쫀쿠 설정, 2026-11-05)
cd "$(dirname "$0")/.." || exit 1
TS=$(date +%Y%m%dT%H%M)
D=logs/monitor/$(date +%Y%m%d); mkdir -p "$D"
{
  echo "=== $TS service=$(systemctl --user is-active week08-myeonggi-market-v4.service)"
  timeout 120 ../../../.venv/bin/python market_track.py 2>&1
} >> "$D/track.log"
timeout 60 curl -s https://market.ourolabs.ai/api/board > "$D/board-$TS.json" 2>/dev/null
cp .market/inventory.json "$D/inventory-$TS.json" 2>/dev/null
find logs/monitor -name "board-*.json" -mtime +5 -delete
