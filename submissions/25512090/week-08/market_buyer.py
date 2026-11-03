import argparse
import json
import os
import sys
from pathlib import Path

def handle_buy(args):
    # Standard buyer deal negotiation / acceptance logic
    print(json.dumps({"action": "accept", "price": args.budget}))

def handle_restock(state_path):
    # Restock logic: sweep commodity anchor SKU (SKU_HARDWARE_TOOL_01) up to max qty (20)
    # Check balance and bankruptcy hard guard (700,000)
    market_dir = Path(state_path).parent if state_path else Path(".market")
    inv_file = market_dir / "inventory.json"
    
    balance = 1000000
    if inv_file.exists():
        try:
            inv = json.loads(inv_file.read_text(encoding="utf-8"))
            balance = inv.get("balance", 1000000)
        except Exception:
            pass

    # Bankruptcy hard guard: if balance <= 700000, stop buying or check bankruptcy
    if balance <= 700000:
        print(json.dumps({"buy": [], "bankrupt": False}))
        return

    # Sweep commodity anchor SKU
    buy_orders = [
        {"sku": "SKU_HARDWARE_TOOL_01", "qty": 20}
    ]
    print(json.dumps({"buy": buy_orders}))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--item")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--restock")
    args = ap.parse_args()

    if args.restock:
        handle_restock(args.restock)
    else:
        handle_buy(args)
