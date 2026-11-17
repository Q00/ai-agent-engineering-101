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

# ---- parallel orders (다나, 2026-11-17): up to buyer.parallel_orders orders at once, one folder per slot
import queue
import threading

N_SLOTS = max(1, min(4, int(params("buyer").get("parallel_orders", 2))))
SHARED = ["payments.jsonl", "catalog.json", "buyer-box", "buyer-public-metadata.json", "demand.json", "forum.json"]


def make_slot(i):
    if i == 0:
        return ROOT
    slot = ROOT / f"runtime/slot{i}"
    (slot / ".market").mkdir(parents=True, exist_ok=True)
    for f in ROOT.glob("*.py"):                 # commands are relative to the folder; scripts resolve back to ROOT
        link = slot / f.name
        if not link.is_symlink():
            link.unlink(missing_ok=True)
            link.symlink_to(f)
    for name in SHARED:
        link, target = slot / ".market" / name, ROOT / ".market" / name
        if target.exists() and not link.is_symlink():
            if link.is_dir():
                shutil.rmtree(link)
            else:
                link.unlink(missing_ok=True)
            link.symlink_to(target)
    return slot


import shutil
SLOTS = queue.Queue()
for _i in range(N_SLOTS):
    SLOTS.put(make_slot(_i))
IN_FLIGHT = set()
original_handle = kit.handle_order


def parallel_handle(o, agent, d, market, proxy, own_shop, keys):
    if o.get("order") in IN_FLIGHT:             # the market gave back an order that is still running
        time.sleep(3)
        return None
    slot = SLOTS.get()                          # waits while every slot is busy
    IN_FLIGHT.add(o.get("order"))

    def work():
        try:
            original_handle(o, agent, slot, market, proxy, own_shop, keys)
        except Exception as e:
            kit.log(f"[kit] order failed in {slot.name}: {type(e).__name__}: {e}")
        finally:
            IN_FLIGHT.discard(o.get("order"))
            SLOTS.put(slot)
    threading.Thread(target=work, daemon=True).start()
    return None


if N_SLOTS > 1:
    kit.handle_order = parallel_handle
    print(f"parallel orders: {N_SLOTS} slots", flush=True)

sys.argv = ["market_kit.py", "join", "--dir", str(ROOT), "--market", "https://market.ourolabs.ai",
            "--name", "cheapest-in-town", "--buyer-only"]
kit.main()
