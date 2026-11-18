"""Preflight by default. --publish is the explicit external-registration gate."""
import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import re

from market_tools import ROOT, build_context
from payments import SchemaAcpShop, pay
from policy import event, load_policy, prepare, RESERVES
from vendor import market_kit_v3 as kit
from competition import live_competition
from scouting import record_scouting
from growth import settings as growth_settings

MARKET = "https://market.ourolabs.ai"
MEMORY_LOCK = threading.RLock()


def write(path, data):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class StrategicMarket(kit.Market):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_order = 0
        self.metrics = json.loads((ROOT / "metrics.json").read_text(encoding="utf-8")) if (ROOT / "metrics.json").exists() else {
            "seller_settled": 0, "buyer_settled": 0, "seller_profit": 0, "seller_revenue": 0,
            "buyer_spend": 0, "wallet_net_flow": 0, "settlements": []}
        self.refresh_at, self.refresh_count, self.day = time.time(), 0, int(time.time()) // 86400
        self.refresh_pending = False
        self.last_cooperation_scan = 0
        self.last_scout = 0

    def call(self, method, path, role="buyer", **kwargs):
        if method == "POST" and path == "/api/orders":
            delay = self.last_order + 45 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last_order = time.monotonic()
        response = super().call(method, path, role=role, **kwargs)
        if response.status_code != 200:
            return response
        try:
            data = response.json()
            if method == "GET" and path == "/api/shops":
                write(ROOT / ".market/shop-keys.json", {s["url"].rstrip("/"): s["pubkey"]
                    for s in data["shops"] if s.get("pubkey")})
                if time.time() - self.last_scout >= 300:
                    self.last_scout = time.time()
                    try:
                        observed_board = super().call("GET", "/api/board?round=3").json()
                        intel = record_scouting(observed_board)
                        event("competitors_observed", observed_round=intel["observed_round"], leaders=len(intel["leaders"]))
                    except Exception as exc:
                        event("scouting_failed", error=type(exc).__name__)
                if time.time() - self.last_cooperation_scan >= 60 and live_competition():
                    self.last_cooperation_scan = time.time()
                    ledger = super().call("GET", "/api/ledger?after=-200").json()
                    entries = ledger if isinstance(ledger, list) else ledger.get("entries", [])
                    order_rounds = {entry.get("data", {}).get("order"): entry.get("data", {}).get("round")
                                    for entry in entries if entry.get("kind") == "order"}
                    for entry in entries:
                        if entry.get("kind") == "settle":
                            # Missing counterparty fields never create alliance credit.
                            copy = {**entry, "data": dict(entry.get("data", {}))}
                            copy["data"].setdefault("round", order_rounds.get(copy["data"].get("order")))
                            live_competition().record_public_return(copy)
            sent = kwargs.get("json", {})
            if method == "POST" and path == "/api/settle" and data.get("settled") is True:
                key = kit.load_key(ROOT / "shop_key.pem")
                checkout = kit.jwt_payload(sent["checkout_jwt"], kit.pub_b64(key))
                self.settled("seller", sent["order"], checkout["item"], checkout["price"])
                if live_competition():
                    live_competition().settle_quote(checkout["task"], checkout["price"])
            if method == "POST" and path == "/api/results" and data.get("verified") and sent.get("mandate_receipt"):
                receipt = sent["receipt"]
                is_new = self.settled("buyer", sent["order"], receipt["item"], receipt["price"])
                if not is_new:
                    return response
                memory_path = ROOT / "trade-memory.json"
                with MEMORY_LOCK:
                    memory = json.loads(memory_path.read_text(encoding="utf-8"))
                    listing = json.loads((ROOT / ".market/shops.json").read_text(encoding="utf-8"))
                    name = next(s["name"] for s in listing if s["url"].rstrip("/") == sent["shop"].rstrip("/"))
                    entry = memory["shops"][name]
                    entry["settled"] = entry.get("settled", 0) + 1
                    entry.setdefault("prices", {})[receipt["item"]] = receipt["price"]
                    if live_competition():
                        for observation in entry.get("history", []):
                            if observation.get("task") == receipt["task"] and observation.get("extra_reservation"):
                                live_competition().resolve_extra(observation["extra_reservation"], True)
                    write(memory_path, memory)
        except Exception as exc:
            event("telemetry_error", type=type(exc).__name__, message=str(exc))
        return response

    def settled(self, role, order, item, price):
        with MEMORY_LOCK:
            marker = f"{role}:{order}"
            journal = ROOT / ".market/counting"
            journal.mkdir(parents=True, exist_ok=True)
            import hashlib
            try:
                (journal / hashlib.sha256(marker.encode()).hexdigest()).touch(exist_ok=False)
            except FileExistsError:
                return False
            self.metrics[f"{role}_settled"] += 1
            self.metrics["seller_profit" if role == "seller" else "buyer_spend"] += price - RESERVES[item] if role == "seller" else price
            if role == "seller":
                self.metrics["seller_revenue"] = self.metrics.get("seller_revenue", 0) + price
            self.metrics["wallet_net_flow"] = self.metrics.get("seller_revenue", 0) - self.metrics["buyer_spend"]
            self.metrics["settlements"].append({"role": role, "item": item, "price": price, "settled": True, "at": time.time()})
            self.metrics["settlements"] = self.metrics["settlements"][-100:]
            write(ROOT / "metrics.json", self.metrics)
            event("confirmed_settlement", role=role, order=order, item=item, price=price,
                  seller_count=self.metrics["seller_settled"], buyer_count=self.metrics["buyer_settled"])
            if int(time.time()) // 86400 != self.day:
                self.day, self.refresh_count = int(time.time()) // 86400, 0
            if not self.refresh_pending and self.refresh_count < 6 and time.time() - self.refresh_at >= 1800:
                self.refresh_pending = True
                threading.Thread(target=self.refresh, daemon=True).start()
            return True

    def refresh(self):
        try:
            self.refresh_count += 1
            context = build_context()
            before = load_policy()
            tmp = ROOT / ".market/new-policy.json"
            asyncio.run(prepare(context, tmp))
            tmp.replace(ROOT / "runtime-policy.json")
            event("policy_repriced", before=before, after=load_policy(), observed=self.metrics)
        except Exception as exc:
            event("policy_refresh_failed", error=str(exc))
        finally:
            self.refresh_at, self.refresh_pending = time.time(), False


def preflight():
    load_policy()
    growth = growth_settings()
    assert kit.KIT_VERSION == "3"
    previous = ROOT.parent / "week-07"
    keys = all((previous / name).exists() for name in ("shop_key.pem", "buyer_key.pem"))
    claim = (previous / ".market/owner-code.txt").exists()
    tunnel = (previous / "tools/cloudflared.exe").exists() if os.name == "nt" else shutil.which("cloudflared") is not None
    for name in ("shop_key.pem", "buyer_key.pem"):
        if (ROOT / name).exists() and (previous / name).exists() and (ROOT / name).read_bytes() != (previous / name).read_bytes():
            raise ValueError("local key differs from the existing market identity")
    return {"ready": keys and claim and tunnel, "published": False, "kit": kit.KIT_VERSION,
            "prior_keys_available": keys, "prior_claim_available": claim, "tunnel_available": tunnel,
            "model_policy": "model-policy.json", "market_posts": 0,
            "growth_tactics": growth,
            "round3_results": "pending; no registration, trade or settlement made by week-08"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    info = preflight()
    if not args.publish:
        print(json.dumps(info, ensure_ascii=True))
        raise SystemExit(0 if info["ready"] else 1)
    # The handoff script sets this only after an explicit -Publish switch and
    # after the user has authorized replacing the previous live operator.
    if os.environ.get("ROUND3_RELEASE_APPROVED") != "yes":
        raise SystemExit("Publication requires the explicit start-round3.ps1 -Publish handoff.")
    if not info["ready"]:
        raise SystemExit("Required local identity/tunnel files are missing; no registration performed.")
    with kit.httpx.Client(timeout=20) as client:
        board = client.get(MARKET + "/api/board?round=3").json()
    if board.get("current", 0) < 3:
        raise SystemExit("Round 3 is not open; no registration performed.")
    previous = ROOT.parent / "week-07"
    (ROOT / ".market").mkdir(exist_ok=True)
    for filename in ("shop_key.pem", "buyer_key.pem"):
        target = ROOT / filename
        if not target.exists():
            shutil.copy2(previous / filename, target)
        elif target.read_bytes() != (previous / filename).read_bytes():
            raise SystemExit("Key mismatch; reconcile identity before joining.")
    claim = (previous / ".market/owner-code.txt").read_text(encoding="utf-8").strip()
    cloudflared = previous / "tools"
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + str(cloudflared) + os.pathsep + os.environ["PATH"]
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["COMPETITION_LIVE"] = "1"
    kit.Market, kit.AcpShop, kit.acp_pay = StrategicMarket, SchemaAcpShop, pay
    original_log = kit.log
    def private_log(line=""):
        # Preserve settlement evidence while keeping private authorization limits
        # out of logs that will later be committed to a public repository.
        original_log(re.sub(r"(\[order [^\]]+\].*?\bbudget\s+)\d+(?:\.\d+)?", r"\1[private]", str(line)))
    kit.log = private_log
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        (ROOT / ".market/operator.pid").write_text(str(os.getpid()), encoding="ascii")
        sys.argv = ["market_kit_v3", "join", "--dir", str(ROOT), "--market", MARKET,
                    "--name", "totato2203", "--tunnel", "--owner", claim]
        kit.main()
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
