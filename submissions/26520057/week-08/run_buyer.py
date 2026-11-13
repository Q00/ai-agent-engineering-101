"""The official kit, buyer-only, paced at most once per 90 seconds after funnel validation."""
import sys
import time
from pathlib import Path

import market_kit as kit
from shop_strategy import params

ROOT = Path(__file__).resolve().parent
original = kit.Market.call
last_order = 0
ORDER_INTERVAL = 90


def paced(self, method, path, *args, **kwargs):
    global last_order
    if method == "POST" and path == "/api/orders":
        time.sleep(max(0, params("buyer")["order_interval"] - (time.monotonic() - last_order)))
        last_order = time.monotonic()
    return original(self, method, path, *args, **kwargs)


kit.Market.call = paced
# buyer posts live in .market/buyer-box/ so the shop process (same folder) never sends them as the shop
original_outbox = kit.outbox
kit.outbox = lambda market, md: original_outbox(market, md / "buyer-box")
sys.argv = ["market_kit.py", "join", "--dir", str(ROOT), "--market", "https://market.ourolabs.ai",
            "--name", "cheapest-in-town", "--buyer-only"]
kit.main()
