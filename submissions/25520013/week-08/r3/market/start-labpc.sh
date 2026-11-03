#!/bin/bash
# Starts the shop loop and the buyer loop in tmux session "market" on labpc-wsl (week-09, R3 v1.16.1).
# MARKET_OWNER comes from ~/.config/lss-market/owner (mode 600); its value is never printed.
set -e
cd "$(dirname "$0")"
export MARKET_OWNER="$(cat ~/.config/lss-market/owner)"
export PATH="$HOME/.local/bin:$PATH"
export R3_MARKET=https://market.ourolabs.ai R3_SHOP_NAME=lee-sungsoo R3_BUYER_NAME=lee-sungsoo
tmux has-session -t market 2>/dev/null && { echo "tmux session market already running"; exit 0; }
tmux new-session -d -s market -n shop "bash $PWD/shop-loop.sh"
tmux new-window -t market -n buyer "bash $PWD/buyer-loop.sh"
echo "started tmux session market (shop, buyer)"
