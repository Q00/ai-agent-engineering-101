"""Challenge 2 -- spins up several copies of seller.py with different item
subsets and price multipliers, waits for each one's Agent Card to come up,
and writes directory.json: the shops' REAL Agent Cards (the actual
/.well-known/agent-card.json documents), not a made-up schema.

Leaves the shop processes running in the background after it writes the
directory -- stop them yourself (pkill -f "seller.py --port 98") once done.

    python spawn_shops.py --count 20 --out directory.json
"""
import argparse
import json
import subprocess
import sys
import time

import httpx

PYTHON = sys.executable   # same interpreter spawn_shops.py itself is running under (has a2a-sdk, uvicorn, httpx)

ITEM_SETS = [
    ["lamp"], ["chair"], ["bookshelf"],
    ["lamp", "chair"], ["lamp", "bookshelf"], ["chair", "bookshelf"],
    ["lamp", "chair", "bookshelf"],
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--base-port", type=int, default=9800)
    ap.add_argument("--out", default="directory.json")
    args = ap.parse_args()

    kw = {"start_new_session": True} if sys.platform != "win32" else {}
    for i in range(args.count):
        port = args.base_port + i
        items = ITEM_SETS[i % len(ITEM_SETS)]
        mult = round(0.85 + 0.05 * (i % 7), 2)
        name = f"Shop {i + 1:02d} ({'/'.join(items)})"
        cmd = [PYTHON, "seller.py", "--port", str(port), "--items", ",".join(items),
               "--price-mult", str(mult), "--name", name]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)

    cards = []
    for i in range(args.count):
        port = args.base_port + i
        deadline = time.time() + 20
        card = None
        while time.time() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=2)
                if r.status_code == 200:
                    card = r.json()
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if card:
            cards.append(card)
            print(f"shop {i + 1:02d} up: {card['name']:<28} -> {card['skills'][0]['description']}")
        else:
            print(f"shop {i + 1:02d}: FAILED to come up on port {port}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)
    print(f"\n{len(cards)} Agent Cards written to {args.out}")


if __name__ == "__main__":
    main()
