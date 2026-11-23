"""The shop's memory, its pricing rule, and receipt signing.

Memory (seller_memory.json, next to the code; the kit and the tests run in this directory):
  trades    every finished negotiation: item, outcome (deal | rejected | canceled | lapsed),
            opening ask, final price, number of offers, A2A context id, time
  margin    per item, the opening ask as a multiple of the reserve
  contexts  per A2A context id, the last negotiation with that buyer, so a buyer who comes back
            in the same context is recognised (multi-turn across tasks)
  members   per member code, the deals made with it. Buyers open a new context for every order, so
            a buyer is recognised by the code the shop hands out with each receipt ("HF-" and six
            letters, no digits: a buyer that reads the first or last number must still read the price)

Pricing: many sales at a small margin. The opening ask is reserve x margin, margin starting at
START_MARGIN and kept between MIN_MARGIN and MAX_MARGIN. After each trade the margin moves:
  a deal at the opening ask (the buyer did not even counter)  -> up by STEP_UP   (we left money)
  a deal after countering                                    -> unchanged
  the buyer walked away or the shop rejected                 -> down by STEP_DOWN (too dear)
The reserve is never crossed; that check stays in seller.py.

Launch pricing (demand). The market lists a shop with its reviews, and buyers pick shops with many
verified Round 2 reviews, so a shop with few visits stays without visits (Round 2 at first: 1 visit
for yms979 against 157 for corner-store). While the shop had fewer than LAUNCH_VISITS negotiations
in the last LAUNCH_WINDOW seconds, the opening ask is at most reserve x LAUNCH_MARGIN and the
target (an offer at or above it is a sale) is the reserve itself: every visitor should leave with a
quick deal and a good review. Once visits come, the margin rule above is back. Every trade records
the mode it was priced in, so the price before and after can be compared from seller_memory.json.

Signing: the receipt data Part is {shop, item, price, task} plus sig_seller, the base64 Ed25519
signature with shop_key.pem over the canonical JSON (keys sorted, no spaces, UTF-8), price a whole
number. The key is read when signing, so it is always the file the kit registered.
"""
import base64
import random
import string
import json
import math
import os
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEMORY = Path(os.environ.get("SELLER_MEMORY", HERE / "seller_memory.json"))
KEY = Path(os.environ.get("SHOP_KEY", HERE / "shop_key.pem"))
START_MARGIN, MIN_MARGIN, MAX_MARGIN = 1.15, 1.05, 1.30
STEP_UP, STEP_DOWN = 0.02, 0.03
RECENT = 20
LOYALTY = ((3, 0.05), (1, 0.03))   # (deals with the code, discount): 1-2 deals 3 %, 3 or more 5 %
LAUNCH_WINDOW = int(os.environ.get("SELLER_LAUNCH_WINDOW", "3600"))
LAUNCH_VISITS = int(os.environ.get("SELLER_LAUNCH_VISITS", "4"))
LAUNCH_MARGIN = 1.02
_lock = threading.Lock()


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(shop: str, item: str, price, task: str) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if not KEY.exists():                      # same as the kit: create it, never commit it
        k = Ed25519PrivateKey.generate()
        KEY.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    key = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
    body = {"shop": shop, "item": item, "price": int(price), "task": task}
    return {**body, "sig_seller": base64.b64encode(key.sign(canon(body))).decode()}


class SellerMemory:
    def __init__(self, path: Path = MEMORY):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("trades", [])
        self.data.setdefault("margin", {})
        self.data.setdefault("contexts", {})
        self.data.setdefault("members", {})

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def margin(self, item_id: str) -> float:
        return self.data["margin"].get(item_id, START_MARGIN)

    def visits(self, window: int = LAUNCH_WINDOW) -> int:
        """Negotiations (any outcome) that ended in the last `window` seconds."""
        since = time.time() - window
        return sum(1 for t in self.data["trades"] if t["t"] >= since)

    def launch(self) -> bool:
        return self.visits() < LAUNCH_VISITS

    def mode(self) -> str:
        return "launch" if self.launch() else "normal"

    def floor_margin(self) -> float:
        """The target as a multiple of the reserve: the reserve itself in launch pricing."""
        return 1.0 if self.launch() else MIN_MARGIN

    def opening_ask(self, item: dict) -> int:
        m = self.margin(item["id"])
        if self.launch():
            m = min(m, LAUNCH_MARGIN)
        return max(item["reserve"], math.ceil(item["reserve"] * m))

    def max_ask(self, item: dict) -> int:
        return math.ceil(item["reserve"] * MAX_MARGIN)

    def summary(self, item_id: str) -> str:
        """One line for the prompt: how this item has been selling lately."""
        ts = [t for t in self.data["trades"] if t["item"] == item_id][-RECENT:]
        if not ts:
            return "No sales of this item yet."
        deals = [t for t in ts if t["outcome"] == "deal"]
        walked = [t for t in ts if t["outcome"] != "deal"]
        avg = round(sum(t["price"] for t in deals) / len(deals)) if deals else None
        avg_offers = round(sum(t["offers"] for t in deals) / len(deals), 1) if deals else None
        return (f"Last {len(ts)} negotiations of this item: {len(deals)} sold"
                + (f" at an average of {avg} after {avg_offers} offers" if deals else "")
                + f", {len(walked)} ended without a sale"
                + (f" (opening asks there averaged {round(sum(t['open_ask'] for t in walked) / len(walked))})" if walked else "")
                + ".")

    def member_deals(self, code: str) -> int:
        return self.data["members"].get(code, {}).get("deals", 0) if code else 0

    def discount(self, code: str) -> float:
        n = self.member_deals(code)
        return next((d for need, d in LOYALTY if n >= need), 0.0)

    def issue(self, code: str = "") -> str:
        """Count a deal for `code`, or hand out a new code. Returns the code."""
        with _lock:
            if code not in self.data["members"]:
                code = "HF-" + "".join(random.choices(string.ascii_uppercase, k=6))
                self.data["members"][code] = {"deals": 0, "since": int(time.time())}
            self.data["members"][code]["deals"] += 1
            self.data["members"][code]["last"] = int(time.time())
            self._save()
        return code

    def recall(self, context_id: str):
        return self.data["contexts"].get(context_id) if context_id else None

    def record(self, item_id: str, outcome: str, open_ask: int, price, offers: int, context_id: str = "",
               last_ask=None, last_offer=None):
        with _lock:
            mode = self.mode()     # before this trade is counted as a visit
            self.data["trades"].append({"t": int(time.time()), "item": item_id, "outcome": outcome,
                                        "open_ask": open_ask, "price": price, "offers": offers, "mode": mode})
            m = self.margin(item_id)
            if outcome == "deal" and offers <= 1:
                m = min(MAX_MARGIN, m + STEP_UP)
            elif outcome != "deal":
                m = max(MIN_MARGIN, m - STEP_DOWN)
            self.data["margin"][item_id] = round(m, 3)
            if context_id:
                self.data["contexts"][context_id] = {"item": item_id, "outcome": outcome, "price": price,
                                                     "last_ask": last_ask, "last_offer": last_offer,
                                                     "t": int(time.time())}
            self._save()
            return m
