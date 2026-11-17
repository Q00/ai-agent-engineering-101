"""Supply execution guards and a separate, minimal settlement observation store.

No credentials in these files. Network calls use the existing authenticated
market-kit object; this module does not join, register, or sign anything.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

from restock_policy import Config, InputError, load_json, number, supply_window


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class SalesStore:
    """Append observations without touching seller storage, buyer memory, or SQLite.

    market_run must change after a market reset. A mismatched run fails closed
    rather than resetting or mixing old observations. One kit process per state.
    """
    def __init__(self, path, market_run, clock=time.time):
        if not market_run:
            raise InputError("explicit market_run required")
        self.path, self.run, self.clock = Path(path), market_run, clock
        self.lock = threading.RLock()
        if self.path.exists() and load_json(self.path).get("market_run") != self.run:
            raise InputError("history belongs to a different market_run; archive policy files before a confirmed reset")

    def _read(self):
        if self.path.exists():
            data = load_json(self.path)
            if data.get("market_run") != self.run:
                raise InputError("history market_run changed")
            return data
        now = self.clock()
        return {"market_run": self.run, "observed_since": now, "as_of": now, "events": []}

    def heartbeat(self):
        with self.lock:
            data = self._read()
            now = self.clock()
            # Do not count a disconnected interval as measured zero demand.
            if now - data["as_of"] > 90:
                data["observed_since"] = now
            data["as_of"] = now
            atomic_json(self.path, data)

    def record(self, *, ledger, sku, price, unit=None, cost=None, ts=None):
        if type(ledger) is not int or ledger < 0 or not isinstance(sku, str):
            raise InputError("cannot map settlement to an exact ledger and SKU")
        number(price, "settled price")
        event = {"id": f"{self.run}:seller:{ledger}", "market_run": self.run, "round": 3,
                 "role": "seller", "settled": True, "sku": sku, "qty": 1, "price": price,
                 "unit": unit, "cost": cost, "ts": self.clock() if ts is None else ts,
                 "source": "local_ACP_completion"}
        with self.lock:
            data = self._read()
            old = next((e for e in data["events"] if e["id"] == event["id"]), None)
            if old:
                if any(old.get(k) != event.get(k) for k in ("sku", "price", "unit", "cost")):
                    raise InputError("conflicting settlement; observation was not overwritten")
                return
            data["events"].append(event)
            data["as_of"] = self.clock()
            atomic_json(self.path, data)


class SupplyGuard:
    """One POST per tick, persistent 10-second and server 429 cooldowns.

    Reserves the worst-case cost *before* sending. An ambiguous transport error
    leaves an unresolved intent and blocks further buying across restarts.
    It requires explicit reconciliation; it never retries an uncertain POST.
    """
    def __init__(self, state, market_run, cfg=None, clock=time.time):
        self.state, self.run = Path(state), market_run
        self.path = self.state / "restock_control.json"
        self.cfg, self.clock = cfg or Config(), clock
        self.lock = threading.RLock()

    def read(self):
        if not self.path.exists():
            return {"market_run": self.run, "next_attempt": 0, "window": None,
                    "remaining": 0, "pending": None}
        c = load_json(self.path)
        if c.get("market_run") != self.run:
            raise InputError("control belongs to a different market_run")
        return c

    def step(self, market, proposal, catalog, window_budget, log=lambda x: None):
        """proposal(fresh_inventory, remaining_budget) runs the registered hook.

        It is called a second time with a newer inventory before POST. This
        recomputes quantities and budget if sales or balances changed while
        the hook was running. Only the first eligible line is sent per tick.
        """
        with self.lock:
            c = self.read()
            now = self.clock()
            if c.get("pending"):
                return {"reason": "unresolved_supply_intent"}
            if now < c["next_attempt"]:
                return {"reason": "cooldown", "next_attempt": c["next_attempt"]}
            response = market.call("GET", "/api/inventory", "shop")
            if response.status_code != 200:
                return {"reason": "inventory_unavailable"}
            inv = response.json()
            if inv.get("round") != 3:
                return {"reason": "not_round3"}
            state = supply_window(inv, now, self.cfg)
            atomic_json(self.state / "inventory.json", inv)
            if state != "open":
                return {"reason": state}
            window = [inv["restock"]["opens_at"], inv["restock"]["closes_at"]]
            if c["window"] != window:
                c.update(window=window, remaining=window_budget(inv), next_attempt=now)
                atomic_json(self.path, c)
            first = proposal(inv, c["remaining"])
            if not first.get("buy"):
                return {"reason": first.get("reason", "no_buy")}
            # Latest inventory and clock are authoritative, not the command's old snapshot.
            response = market.call("GET", "/api/inventory", "shop")
            if response.status_code != 200:
                return {"reason": "inventory_recheck_unavailable"}
            inv = response.json()
            now = self.clock()
            atomic_json(self.state / "inventory.json", inv)
            if inv.get("round") != 3 or supply_window(inv, now, self.cfg) != "open":
                return {"reason": "supply_changed_before_post"}
            if window != [inv["restock"]["opens_at"], inv["restock"]["closes_at"]]:
                return {"reason": "window_changed_before_post"}
            latest = proposal(inv, c["remaining"])
            if not latest.get("buy"):
                return {"reason": "proposal_no_longer_needed"}
            now = self.clock()
            if supply_window(inv, now, self.cfg) != "open":
                return {"reason": "hook_finished_after_window"}
            b = latest["buy"][0]
            sku, qty = b.get("sku"), b.get("qty")
            if sku not in catalog or type(qty) is not int or not 1 <= qty <= 20:
                raise InputError("invalid hook buy line")
            cost_upper = math.ceil(number(catalog[sku].get("list_price"), "list_price", 1) * .75) * qty
            reserve = max(self.cfg.reserve_floor, math.ceil(inv["balance"] * self.cfg.reserve_fraction))
            if cost_upper > min(c["remaining"], max(0, inv["balance"] - reserve)):
                return {"reason": "cash_changed_before_post"}
            # Include risk/storage buffers from the recomputed policy when debiting the window.
            per_unit = latest.get("unit_commitment_upper", {}).get(sku, cost_upper / qty)
            commitment = math.ceil(number(per_unit, "unit commitment")) * qty
            if commitment < cost_upper:
                raise InputError("hook commitment is below the cost envelope")
            if commitment > c["remaining"]:
                return {"reason": "window_budget_exhausted"}
            c["remaining"] -= commitment
            c["next_attempt"] = now + 10
            c["pending"] = {"sku": sku, "qty": qty, "at": now, "reserved": commitment}
            atomic_json(self.path, c)
            try:
                r = market.call("POST", "/api/supply", "shop", json={"sku": sku, "qty": qty})
            except Exception as exc:
                log(f"[restock] uncertain supply outcome ({type(exc).__name__}); buying paused for reconciliation")
                return {"reason": "ambiguous_supply_result"}
            # A malformed success or 5xx could hide a committed purchase.
            try:
                body = r.json()
            except (ValueError, TypeError):
                return {"reason": "ambiguous_supply_response"}
            if r.status_code == 200:
                try:
                    total = number(body.get("total"), "supply.total")
                except (InputError, AttributeError):
                    return {"reason": "ambiguous_supply_response"}
                if total > cost_upper:
                    # Price envelope was violated; do not silently spend again.
                    return {"reason": "supply_cost_bound_violated"}
                c["pending"] = None
                # Keep conservative reservation: unused buffers are not recycled this window.
                atomic_json(self.path, c)
                log(f"[restock] bought {qty} x {sku}; actual cost {total}; window budget left {c['remaining']}")
                return {"reason": "bought", "sku": sku, "qty": qty, "actual_cost": total}
            if r.status_code == 429:
                # No sleep and no same-tick retry. Resume at the maximum wait signal.
                wait = number(body.get("wait", 10), "429.wait")
                opens = number(body.get("opens_at", now), "429.opens_at")
                c["next_attempt"] = max(now + 10, now + wait, opens)
            elif r.status_code == 402:
                c["next_attempt"] = window[1]
            elif 400 <= r.status_code < 500:
                c["next_attempt"] = now + 60
            else:
                return {"reason": "ambiguous_supply_server_error"}
            c["remaining"] += commitment  # authoritative refusal: nothing purchased
            c["pending"] = None
            atomic_json(self.path, c)
            return {"reason": f"supply_refused_{r.status_code}", "next_attempt": c["next_attempt"]}





