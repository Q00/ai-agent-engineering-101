#!/bin/bash
# Starts the kit loop (shop and buyer in one process) and the forum loop in tmux session "market" on labpc-wsl
# (week-09, R3 v1.17). One kit process: the shop-only and buyer-only kits both sent .market/outbox.jsonl, and the
# shop one posts a line written "as": "buyer" as the shop (market_kit.py outbox()).
# MARKET_OWNER comes from ~/.config/lss-market/owner (mode 600); its value is never printed.
set -e
cd "$(dirname "$0")"
export MARKET_OWNER="$(cat ~/.config/lss-market/owner)"
export PATH="$HOME/.local/bin:$PATH"
export R3_MARKET=https://market.ourolabs.ai R3_SHOP_NAME=lee-sungsoo R3_BUYER_NAME=lee-sungsoo
tmux has-session -t market 2>/dev/null && { echo "tmux session market already running"; exit 0; }
tmux new-session -d -s market -n kit "bash $PWD/kit-loop.sh"
tmux new-window -t market -n forum "bash $PWD/forum-loop.sh"
echo "started tmux session market (kit, forum)"
