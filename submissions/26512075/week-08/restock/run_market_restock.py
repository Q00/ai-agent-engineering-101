"""Run the unchanged official v4 kit with guarded restocking and sale observation.

Example (existing keys and seller/buyer state are reused by the official kit):
 python run_market_restock.py --kit ../../../scripts/market_kit.py \
   --market-run round3-20261104 -- join --dir . --market URL --name NAME --tunnel
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
import forum_effects
import participation_evidence

from restock_evidence import attach_pilot_state

from restock_policy import Config, InputError, load_json, plan, read_inputs
from restock_runtime import SalesStore, SupplyGuard


# Reviewed official file, fetched 2026-11-04. Any update requires review and tests.
REVIEWED_KIT_SHA256 = "4a0f48b33a6c0b207401325cd1e48086c364a0c89177fb5b347703affe774272"


def observe_completion(store, before, completed):
    if completed.get("status") != "completed":
        return
    mr = completed["order"]["mandate_receipt"]
    sku = completed["line_items"][0]["id"]
    unit = mr.get("unit") or completed.get("unit")
    old = next((u for u in before.get("units", []) if u.get("unit") == unit), {})
    if before.get("round") != 3 or old.get("sku") != sku:
        raise InputError("settlement stock/round mapping missing; observation skipped")
    store.record(ledger=mr["ledger"], sku=sku, price=completed["totals"][0]["amount"],
                 unit=unit, cost=old.get("cost"), grade=mr.get("grade"))


def install_runtime(kit, state, market_run, cfg):
    # Retain ten times the official kit's 20 MB per-file log capacity.
    # Both submission launchers install this runtime before kit.main().
    kit.LOG_MAX = 200_000_000
    store = SalesStore(state / "restock_sales.json", market_run)
    guard = SupplyGuard(state, market_run, cfg)
    guard.read()  # Reject incompatible run before starting the official kit.
    os.environ["RESTOCK_GUARDED_RUN"] = market_run
    original_restock = kit.restock
    original_acp = kit.AcpShop
    observation_lock = threading.RLock()
    last_inventory = None

    def capture(inventory, reason):
        nonlocal last_inventory
        payload = json.dumps(inventory, sort_keys=True)
        with observation_lock:
            if reason == "post_supply" or payload != last_inventory:
                participation_evidence.snapshot(state, inventory, reason=reason, market_run=market_run)
                last_inventory = payload


    class ObservedShop(original_acp):
        async def handle(self, req, path, body, public):
            observe = (self.md is not None and req.method == "POST" and path.endswith("/complete"))
            before = {}
            if observe:
                try:
                    before = load_json(self.md / "inventory.json")
                except (OSError, ValueError):
                    pass
            result = await super().handle(req, path, body, public)
            if observe and result.status_code == 200:
                try:
                    with observation_lock:
                        completed = json.loads(result.body)
                        # Read the task from the shop-signed checkout, never from buyer text.
                        checkout = kit.jwt_payload(completed.get("checkout_jwt", ""), self.pub) or {}
                        task_id = checkout.get("task")
                        participation_evidence.completion(state, before, completed,
                                                          market_run=market_run, task_id=task_id)
                        if completed.get("status") == "completed":
                            kit.log("[settlement] " + json.dumps({
                                "task": task_id, "sku": completed["line_items"][0]["id"],
                                "unit": completed["order"]["mandate_receipt"].get("unit"),
                                "ledger": completed["order"]["mandate_receipt"]["ledger"],
                                "price": completed["totals"][0]["amount"]}, ensure_ascii=False))
                        observe_completion(store, before, completed)
                        forum_effects.settled(state, before, completed, public)
                except Exception as exc:
                    kit.log(f"[restock] sale observation skipped ({type(exc).__name__}); settlement unchanged")
            return result

    kit.AcpShop = ObservedShop

    def guarded_restock(agent, d, market, md, inv, demand):
        try:
            if inv.get("round") == 3:
                store.heartbeat()
                capture(inv, "inventory_poll")
                forum_effects.snapshot(state, inv)
        except (OSError, ValueError) as exc:
            kit.log(f"[observation] unavailable: {type(exc).__name__}")
        command = (agent.get("restock") or {}).get("cmd", "")
        if "restock_policy.py" not in command:
            # A hold/custom command remains authoritative. No surprise stock buying.
            return original_restock(agent, d, market, md, inv, demand)
        try:
            # Only a successful current Round 3 inventory poll advances observation time.
            if inv.get("round") != 3:
                return
            store.heartbeat()
            catalog = load_json(md / "catalog.json")

            def proposed(fresh, remaining):
                # Contract is exercised: the kit still runs agent.json's restock command.
                output = kit.last_json(kit.run_buyer(kit.fill(command, state=md), d)) or {}
                # Independently recalculate from freshest data. Intersect with the hook
                # so a command returning [] (including invalid input) always vetoes buys.
                now = time.time()
                _, cat, dem, history = read_inputs(md, now=now, cfg=cfg)
                attach_pilot_state(fresh, guard.read(), history, cfg)
                latest = plan(fresh, cat, dem, history, now=now, cfg=cfg, window_remaining=remaining)
                requested = output.get("buy")
                if not isinstance(requested, list):
                    requested = []
                allowed = {b["sku"]: b["qty"] for b in requested if isinstance(b, dict)
                           and isinstance(b.get("sku"), str) and type(b.get("qty")) is int and b["qty"] > 0}
                latest["buy"] = [{"sku": b["sku"], "qty": min(b["qty"], allowed[b["sku"]])}
                                 for b in latest["buy"] if b["sku"] in allowed]
                return latest

            def window_budget(fresh):
                now = time.time()
                _, cat, dem, history = read_inputs(md, now=now, cfg=cfg)
                attach_pilot_state(fresh, guard.read(), history, cfg)
                return plan(fresh, cat, dem, history, now=now, cfg=cfg)["budget"]

            result = guard.step(market, proposed, catalog, window_budget, kit.log)
            kit.log(f"[restock] {result['reason']}")
            if result["reason"] == "bought":
                # Read immediately after supply; later polling cannot replace this evidence.
                response = market.call("GET", "/api/inventory", "shop")
                if response.status_code != 200:
                    kit.log("[evidence] post-supply inventory unavailable; purchase remains confirmed")
                else:
                    capture(response.json(), "post_supply")
        except httpx.RequestError as exc:
            # GET failures may be retried on the next kit tick. Supply POST
            # ambiguity remains protected by SupplyGuard's persisted intent.
            kit.log(f"[restock] network unavailable ({type(exc).__name__}); deferred to next poll")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            kit.log(f"[restock] stopped safely ({type(exc).__name__}: {exc})")

    kit.restock = guarded_restock
    kit.RESTOCK_EVERY = 10  # Window polling; SupplyGuard enforces actual request spacing.
    return store, guard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--market-run", required=True)
    parser.add_argument("--config")
    parser.add_argument("kit_args", nargs=argparse.REMAINDER)
    a = parser.parse_args()
    args = a.kit_args[1:] if a.kit_args[:1] == ["--"] else a.kit_args
    if not args or args[0] != "join":
        parser.error("wrapper supports the kit's join command only")
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("--dir", default=".")
    known, _ = probe.parse_known_args(args)
    submission = Path(known.dir).resolve()
    state = submission / ".market"
    # Official load_key creates keys when missing. Preserve identity by refusing
    # to start in a directory that does not already hold both existing keys.
    if any(not (submission / name).is_file() for name in ("shop_key.pem", "buyer_key.pem")):
        raise SystemExit("Existing shop_key.pem and buyer_key.pem are required; this wrapper will not create replacement identities.")
    agent = load_json(submission / "agent.json")
    command = (agent.get("restock") or {}).get("cmd", "")
    if "restock_policy.py" not in command or "{state}" not in command:
        raise SystemExit("Connect the reviewed restock_policy.py command in agent.json before starting this wrapper.")
    if "--deployed" in args or any(x.startswith("--deployed=") for x in args):
        raise SystemExit("This wrapper observes the kit-proxied seller. A deployed seller needs a separately reconciled settlement feed and inventory integration.")
    cfg = Config(**load_json(a.config)) if a.config else Config()
    kit_path = Path(a.kit).resolve()
    if hashlib.sha256(kit_path.read_bytes()).hexdigest() != REVIEWED_KIT_SHA256:
        raise SystemExit("Official kit changed: review the diff and rerun tests before updating the SHA256 pin.")
    # Exactly one process may write this state. Existing files survive on exit.
    state.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir = state / "restock_runtime.lock"
        lock_dir.mkdir()
    except FileExistsError:
        raise SystemExit("Restock runtime is already active, or a crash left its lock. Verify no kit is running before removing restock_runtime.lock.")
    try:
        spec = importlib.util.spec_from_file_location("official_market_kit_restock", kit_path)
        kit = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = kit
        spec.loader.exec_module(kit)
        install_runtime(kit, state, a.market_run, cfg)
        sys.argv = [str(kit_path), *args]
        kit.main()
    finally:
        lock_dir.rmdir()


if __name__ == "__main__":
    main()
