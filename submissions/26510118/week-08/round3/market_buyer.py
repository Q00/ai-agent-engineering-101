"""Round 3 basic market buyer: the kit runs it once per order and reads its last line.

    python market_buyer.py --shops .market/shops.json --item 김치 --budget 22000 --order .market/order.json
    ...
    {"shop": "<url>", "receipt": {...} | null, "stars": 1-5, "review": "...", "accept": {...}}

Rules only; the enhanced version replaces them:
  - candidates are the (shop, SKU) pairs in shops.json's offers that the order
    accepts; food whose whole shelf life is shorter than use_within_hours can
    never serve the order and is dropped
  - pick the dearest list price within the budget (the surplus is list price
    minus price paid), else the cheapest candidate
  - offer 70, 80, 90, 100% of min(budget, list price) and never more: paying
    over the list price loses surplus even inside the budget; take the shop's
    ask once it fits under the next offer
  - food: the kit pays only for a certified unit with use_within_hours left
    (plus an hour), checked against the certificate before any money moves
"""
import argparse
import asyncio
import json
import random
import re
import uuid
from pathlib import Path

import httpx

import common

LADDER = (0.70, 0.80, 0.90, 1.00)     # offers, as fractions of min(budget, list price)
MARGIN_HOURS = 1                      # food must last this much longer than the order needs
TIMEOUT = 30                          # seconds per A2A request; the kit stops a buyer after 300
NUMBER = re.compile(r"(?<!\w)\d[\d,]*(?!\d)")


class A2A:
    """A2A 1.0 JSON-RPC client: the card, then SendMessage and CancelTask (week 06)."""

    def __init__(self):
        self.http = httpx.AsyncClient(timeout=TIMEOUT)
        self.url = None

    async def open(self, base: str) -> dict:
        card = (await self.http.get(base.rstrip("/") + "/.well-known/agent-card.json")).json()
        ifaces = card.get("supportedInterfaces") or [{}]
        iface = next((i for i in ifaces if i.get("protocolBinding") == "JSONRPC"), ifaces[0])
        self.url = iface.get("url") or base
        return card

    async def rpc(self, method: str, params: dict):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = (await self.http.post(self.url, json=body, headers={"A2A-Version": "1.0"})).json()
        if "error" in r:
            print(f"[buyer] <- error {r['error'].get('code')} {str(r['error'].get('message'))[:120]}", flush=True)
            return None
        return r["result"].get("task", r["result"])

    async def send(self, text: str, task_id: str | None = None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        print(f"[buyer] -> {text!r}", flush=True)
        t = await self.rpc("SendMessage", {"message": msg})
        if t:
            print(f"[buyer] <- {state(t)} | {said(t)[:120]}", flush=True)
        return t

    async def cancel(self, task_id: str):
        try:
            await self.rpc("CancelTask", {"name": f"tasks/{task_id}", "id": task_id})
        except Exception:                                        # noqa: BLE001
            pass

    async def close(self):
        await self.http.aclose()


def state(t) -> str:
    return (t or {}).get("status", {}).get("state", "")


def said(t) -> str:
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def asked(t):
    """The shop's ask: metadata.ask when it sets one, else the last number it said."""
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    if isinstance(meta.get("ask"), (int, float)):
        return int(meta["ask"])
    nums = [int(n.replace(",", "")) for n in NUMBER.findall(said(t)) if n.replace(",", "").isdigit()]
    return nums[-1] if nums else None


def receipt_of(t):
    for a in (t or {}).get("artifacts") or []:
        for p in a.get("parts") or []:
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def candidates(order: dict, shops: list, catalog: dict) -> list:
    out = []
    for sh in shops:
        for o in sh.get("offers") or []:
            c = catalog.get(o.get("sku")) or {}
            if o.get("sku") not in order.get("acceptable", []) or not o.get("qty") or not c.get("list_price"):
                continue
            if order.get("kind") == "food" and (c.get("shelf_hours") or 0) < (order.get("use_within_hours") or 0):
                continue
            out.append({"shop": sh, "sku": o["sku"], "name": o.get("name") or c.get("name"), "list": c["list_price"]})
    return out


def pick(cands: list, budget: int):
    random.shuffle(cands)                                       # spread ties over shops
    within = [c for c in cands if c["list"] <= budget]
    return max(within, key=lambda c: c["list"]) if within else min(cands, key=lambda c: c["list"])


def accept_for(order: dict) -> dict:
    if order.get("kind") == "food" and order.get("use_within_hours"):
        return {"cert": True, "min_hours_left": order["use_within_hours"] + MARGIN_HOURS}
    return {}


async def trade(client: A2A, c: dict, cap: int) -> dict:
    r = {"deal": False, "receipt": None, "price": None, "asks": [], "offers": [], "error": None}
    try:
        await client.open(c["shop"]["url"])
        t = await client.send(f"I want to buy {c['name']} ({c['sku']})")
        for frac in LADDER:
            if state(t) != "TASK_STATE_INPUT_REQUIRED":
                break
            ask = asked(t)
            if ask is not None:
                r["asks"].append(ask)
            offer = int(cap * frac)
            if ask is not None and ask <= offer:
                offer = ask                                     # it already fits: take it
            r["offers"].append(offer)
            t = await client.send(str(offer), t["id"])
        if state(t) == "TASK_STATE_COMPLETED":
            rec = receipt_of(t)
            # the receipt must be what was agreed: this SKU, at no more than my last offer
            if rec and rec.get("item") == c["sku"] and r["offers"] and float(rec.get("price", 1e18)) <= r["offers"][-1]:
                r.update(deal=True, receipt=rec, price=int(float(rec["price"])))
            else:
                print(f"[buyer] the shop closed but its receipt is not what was agreed: {rec}", flush=True)
        elif state(t) == "TASK_STATE_INPUT_REQUIRED":
            await client.cancel(t["id"])
    except Exception as e:                                       # noqa: BLE001  (a hung or broken shop)
        r["error"] = type(e).__name__
        print(f"[buyer] trade broke off: {type(e).__name__}: {e}", flush=True)
    return r


async def amain(a) -> dict:
    order = common.read_json(a.order, {}) or {}
    shops = common.read_json(a.shops, []) or []
    budget = a.budget
    cands = candidates(order, shops, common.catalog(Path(a.order).parent)) if order.get("acceptable") else []
    if not cands:
        common.record("orders", order=order.get("order"), category=order.get("item"), budget=budget,
                      kind=order.get("kind"), candidates=0, deal=False)
        return {"shop": "", "receipt": None, "stars": 3,
                "review": f"No shop had an acceptable {a.item} in stock within reach.", "accept": {}}
    c = pick(cands, budget)
    cap = min(budget, c["list"])
    print(f"[buyer] {len(cands)} candidate(s); {c['shop']['name']} {c['sku']} list {c['list']}, cap {cap}", flush=True)
    client = A2A()
    try:
        r = await trade(client, c, cap)
    finally:
        await client.close()
    common.record("orders", order=order.get("order"), category=order.get("item"), budget=budget,
                  kind=order.get("kind"), use_within_hours=order.get("use_within_hours"), candidates=len(cands),
                  shop=c["shop"]["name"], sku=c["sku"], list_price=c["list"], cap=cap, asks=r["asks"],
                  offers=r["offers"], deal=r["deal"], price=r["price"], error=r["error"])
    if r["deal"]:
        review = f"Agreed {r['price']} for {c['name']} (list {c['list']}) after {len(r['offers'])} offer(s)."
        stars = 4                                               # the review hook rewrites it once the deal settles
    else:
        review = (f"No deal on {c['name']}: the shop stopped answering." if r["error"] else
                  f"No deal on {c['name']} within {cap}; the lowest ask was {min(r['asks']) if r['asks'] else 'none'}.")
        stars = 3
    return {"shop": c["shop"]["url"], "receipt": r["receipt"], "stars": stars, "review": review,
            "accept": accept_for(order)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", default=str(common.MARKET_DIR / "order.json"))
    common.last_line(asyncio.run(amain(ap.parse_args())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
