"""Persistent PC deployment using the unmodified course market kit.

Only this operator integrates with the market, through the kit's Market client.
Seller and buyer communicate over A2A and read local feedback/public-key files.
"""
from __future__ import annotations

import asyncio
import ctypes
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

from harness import decide, emit

ROOT = Path(__file__).resolve().parent
KIT_PATH = ROOT.parents[2] / "scripts" / "market_kit.py"
MARKET = "https://market.ourolabs.ai"
NAME = "totato2203"
SPEC = importlib.util.spec_from_file_location("course_market_kit", KIT_PATH)
kit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kit)

PRICE_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"opening_margin": {"type": "number"}, "rationale": {"type": "string"}},
    "required": ["opening_margin", "rationale"]}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def record(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=True) + "\n")


class ObservedMarket(kit.Market):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_order, self.observing = 0, False
        self.stop_observer = threading.Event()

    def join(self, role, name, key, url=None):
        result = super().join(role, name, key, url)
        if role == "buyer" and not self.observing:
            self.observing = True
            threading.Thread(target=self.observe, daemon=True).start()
        return result

    def call(self, method, path, role="buyer", **kwargs):
        if method == "POST" and path == "/api/orders":
            interval = float(os.environ.get("MARKET_ORDER_INTERVAL", "45"))
            delay = self.last_order + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last_order = time.monotonic()
        response = super().call(method, path, role=role, **kwargs)
        if method == "GET" and path == "/api/shops" and response.status_code == 200:
            listing = response.json()["shops"]
            write_json(ROOT / ".market" / "shop-keys.json",
                       {s["url"].rstrip("/"): s["pubkey"] for s in listing if s.get("pubkey")})
        return response

    def observe(self):
        previous = None
        try:
            for rnd in (0, 1):
                path = ROOT / "logs" / "market-rounds" / f"round-{rnd}.json"
                if not path.exists():
                    res = super().call("GET", f"/api/board?round={rnd}")
                    res.raise_for_status()
                    write_json(path, res.json())
            while not self.stop_observer.is_set():
                response = super().call("GET", "/api/board?round=2")
                response.raise_for_status()
                board = response.json()
                row = next((s for s in board["shops"] if s["name"] == NAME), None)
                if row:
                    deals = row.get("deals", 0)
                    total = sum(s.get("deals", 0) for s in board["shops"])
                    metrics = {"deals": deals, "visits": row.get("visits", 0),
                               "revenue": row.get("revenue", 0), "share": row.get("share"),
                               "exact_share": deals / total if total else 0,
                               "average_price": row.get("revenue", 0) / deals if deals else None,
                               "stars": row.get("stars"), "reviews": row.get("reviews")}
                    at = datetime.now(timezone.utc).isoformat()
                    current = {"at": at, "metrics": metrics,
                               "shop": row, "buyer": next((b for b in board["buyers"] if b["name"] == NAME), None)}
                    if metrics != previous:
                        record(ROOT / "logs" / "challenge-1" / "observations.jsonl", current)
                        previous = metrics
                    # Adapt once after an actual counted own sale. Do not invent
                    # before/after revenue or treat a local test as a market trade.
                    policy = ROOT / "pricing-feedback.json"
                    evidence = ROOT / "logs" / "challenge-1" / "adaptation.json"
                    if deals and not evidence.exists():
                        own_reviews = [r for r in board.get("reviews", []) if r.get("shop") == NAME][:6]
                        # /api/shops retains reviews even if the board's global
                        # most-recent-review window no longer contains ours.
                        listing = super().call("GET", "/api/shops").json()["shops"]
                        own = next((s for s in listing if s["name"] == NAME), {})
                        own_reviews = own.get("reviews", own_reviews)
                        def validate(answer):
                            margin = answer["opening_margin"]
                            if isinstance(margin, bool) or not 0 <= margin <= 0.25 or margin == 0.2:
                                raise ValueError("choose a changed opening margin between 0 and 0.25; reserves stay fixed")
                        changed = asyncio.run(decide("price_adapt", {
                            "own_metrics": metrics, "current_opening_margin": 0.2,
                            "reserves": {"lamp": 100, "chair": 180, "bookshelf": 260},
                            "goal": "Change the opening-price guidance using your own trades and reviews. "
                                    "Small market share suggests a more competitive margin; never change reserves."},
                            {"own_reviews": own_reviews}, schema=PRICE_SCHEMA, validator=validate))
                        write_json(evidence, {"at": at, "before": current,
                            "before_opening_margin": 0.2, "after_policy": changed, "reviews_read": own_reviews})
                        write_json(policy, changed)
                        kit.log(f"[pricing] changed opening margin 0.2 -> {changed['opening_margin']}; reserves unchanged")
                    if evidence.exists():
                        before = json.loads(evidence.read_text(encoding="utf-8"))["before"]["metrics"]
                        if metrics["deals"] > before["deals"]:
                            after = ROOT / "logs" / "challenge-1" / "after.json"
                            if not after.exists():
                                write_json(after, current)
                                kit.log(f"[pricing] after-change deals={deals}, avg={metrics['average_price']}, share={metrics['exact_share']}")
                    # Keep a public snapshot for the report, not tokens or keys.
                    snapshot = ROOT / "logs" / "market-rounds" / "first-round-2.json"
                    if not snapshot.exists():
                        write_json(snapshot, board)
                self.stop_observer.wait(30)
        except Exception as exc:
            kit.log(f"[observer] {type(exc).__name__}: {exc}")


kit.Market = ObservedMarket


if __name__ == "__main__":
    os.chdir(ROOT)
    os.environ["PATH"] = str(ROOT / "tools") + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    os.environ["PYTHONIOENCODING"] = "utf-8"
    claim = (ROOT / ".market" / "owner-code.txt").read_text(encoding="utf-8").strip()
    (ROOT / ".market" / "operator.pid").write_text(str(os.getpid()), encoding="ascii")
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        while True:
            sys.argv = [str(KIT_PATH), "join", "--dir", str(ROOT), "--market", MARKET,
                        "--name", NAME, "--tunnel", "--owner", claim]
            try:
                kit.main()
            except (Exception, SystemExit) as exc:
                kit.log(f"[operator] kit stopped ({type(exc).__name__}); reconnecting in 30 seconds")
            time.sleep(30)
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
