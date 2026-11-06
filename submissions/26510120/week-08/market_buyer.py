"""Market buyer, Round 3 executor: code picks the shop and the SKU and negotiates; numbers come from policy.json.

    python market_buyer.py --shops .market/shops.json --item 김치 --budget 50000 --order .market/order.json

Week-08 change: no harness call per order. The strategist (strategist.py, a model every ~20 min) sets the policy;
this file follows it.
  pick      every (shop, SKU) the order can use is scored as P(paid) x expected saving. P(paid) is this buyer's own
            record with the shop (attempts in buyer_memory.jsonl, paid deals in .market/payments.jsonl, broken goods
            in .market/broken.jsonl); the expected saving is 1 - the SKU's market median price/list (the ledger).
  negotiate opens at buyer_open x list and climbs to min(budget, buyer_max x list); takes the shop's ask once it
            is under that cap and at or under the market median, or after 3 rounds. Reads the shop's first answer
            for a grade or shelf life that the accept policy would refuse, and moves on to the next shop then.
  accept    food: the certificate must show use_within_hours + buyer_hours_margin of shelf life; goods: grade in
            buyer_grades_goods. The kit checks the checkout against it before paying.
The last output line is the result for the kit.
"""
import argparse
import json
import re
import time
import uuid
from pathlib import Path

import httpx

import market_tool as mt

MAX_OFFERS = 6
DEADLINE_S = 240                       # the kit stops a buyer after 300 s
MEMORY = Path("buyer_memory.jsonl")    # one JSON line per attempt; shops remembered by name
MD = Path(".market")


def load_jsonl(p):
    if not p.exists():
        return []
    out = []
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def shop_record(names, cat):
    """shop name -> {tries, paid, broken} from this buyer's own files (Round 3 lines only)."""
    rec = {}
    for m in load_jsonl(MEMORY):
        if "sku" in m:                                   # week-08 lines; week-07 lines have no SKU
            rec.setdefault(m["shop"], {"tries": 0, "paid": 0, "broken": 0})["tries"] += 1
    paid_names = {}
    for p in load_jsonl(MD / "payments.jsonl"):
        name = names.get(p["shop"].rstrip("/")) or p["shop"].split("/shop/")[-1]
        if p.get("paid"):
            rec.setdefault(name, {"tries": 0, "paid": 0, "broken": 0})["paid"] += 1
            paid_names.setdefault((cat.get(p.get("item")) or {}).get("name"), []).append(name)
    for b in load_jsonl(MD / "broken.jsonl"):
        for name in paid_names.get(b.get("name"), [])[-1:]:   # the latest shop I bought that item from
            rec[name]["broken"] += 1
    return rec


def candidates(shops, order, pol, stats, cat, rec):
    out = []
    for s in shops:
        r = rec.get(s["name"], {"tries": 0, "paid": 0, "broken": 0})
        p_paid = min(1.0, (r["paid"] + 1) / (r["tries"] + 2))  # Laplace: a new shop starts at 1/2
        if r["paid"]:
            p_paid *= max(0.2, 1 - r["broken"] / r["paid"])
        for o in s.get("offers") or []:
            c = cat.get(o["sku"]) or {}
            lp = c.get("list_price")
            if not lp:
                continue
            st = stats.get(o["sku"]) or {}
            ratio = st["ratio"] if st.get("n", 0) >= 3 else 0.72
            if lp * min(ratio, pol["buyer_max"]) > order["budget"]:
                p = p_paid * 0.3                                # likely above the budget
            else:
                p = p_paid
            out.append({"shop": s, "sku": o["sku"], "name": o["name"], "list": lp, "ratio": ratio,
                        "score": p * (1 - ratio), "p_paid": round(p, 3), "qty": o.get("qty", 0)})
    out.sort(key=lambda x: (-x["score"], -x["qty"]))
    picked, seen = [], set()
    for c in out:                                               # one SKU per shop, best first
        if c["shop"]["name"] not in seen:
            picked.append(c)
            seen.add(c["shop"]["name"])
    return picked


def accept_policy(order, pol):
    if order.get("kind") == "food" and order.get("use_within_hours"):
        return {"min_hours_left": round(order["use_within_hours"] + pol["buyer_hours_margin"], 2)}
    if order.get("kind") == "goods":
        return {"grades": pol["buyer_grades_goods"]}
    return {}


def numbers(text, lp):
    """Prices in a shop's message: numbers from 20% to 200% of the list price, in order."""
    nums = [int(x.replace(",", "")) for x in re.findall(r"(?<![A-Za-z#\d])\d[\d,]*", text)]
    return [n for n in nums if 0.2 * lp <= n <= 2 * lp]


def refuses(text, accept):
    """What in the shop's first answer the accept policy would refuse (None when nothing is said)."""
    t = text.lower()
    if "min_hours_left" in accept:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h\b|hours?|시간)", t)
        if m and float(m.group(1)) < accept["min_hours_left"]:
            return f"{m.group(1)} h of shelf life, need {accept['min_hours_left']}"
    if "grades" in accept:
        m = re.search(r"grade[:\s]*([abc])\b|등급\s*([abc])\b", t)
        g = m and (m.group(1) or m.group(2)).upper()
        if g and g not in accept["grades"]:
            return f"grade {g}"
    return None


def negotiate(c, order, pol, accept, until):
    """One shop. Returns (outcome, receipt or None, transcript, last price sent)."""
    card = httpx.get(c["shop"]["url"].rstrip("/") + "/.well-known/agent-card.json", timeout=10).json()
    url = card["supportedInterfaces"][0]["url"]

    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=60).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = rpc("SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        print(f"-> {text!r}  <- {t['status']['state']} | {said[:200]}", flush=True)
        return t, said

    lp = c["list"]
    cap = min(order["budget"], round(lp * pol["buyer_max"]))
    fair = round(lp * c["ratio"])
    ask_text = f"I want to buy {c['name']} ({c['sku']})"
    t, said = send(ask_text)
    lines = [f"you: {ask_text}", f"shop: {said}"]
    if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
        return ("rejected" if t["status"]["state"] == "TASK_STATE_REJECTED" else "ended"), None, lines, None
    why = refuses(said, accept)
    if why:
        rpc("CancelTask", {"id": t["id"]})
        return f"skipped: {why}", None, lines, None
    ask = (numbers(said, lp) or [None])[-1]
    sent = None
    for k in range(MAX_OFFERS):
        if time.time() > until:
            break
        if ask is not None and ask <= cap and (ask <= fair or k >= 3):
            price = ask                                         # take the shop's price
        else:
            step = round(lp * pol["buyer_open"] + (cap - lp * pol["buyer_open"]) * k / (MAX_OFFERS - 1))
            price = min(cap, step if ask is None else min(step, ask))
        sent = int(price)
        t, said = send(str(sent), t["id"])
        lines += [f"you: {sent}", f"shop: {said}"]
        state = t["status"]["state"]
        if state == "TASK_STATE_COMPLETED":
            for a in t.get("artifacts", []):
                for p in a.get("parts", []):
                    d = p.get("data")
                    if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                        return "deal", d, lines, sent
            return "completed without a receipt", None, lines, sent
        if state != "TASK_STATE_INPUT_REQUIRED":
            return "rejected", None, lines, sent
        got = numbers(said, lp)
        ask = got[-1] if got else ask
    rpc("CancelTask", {"id": t["id"]})
    return f"walked (shop at {ask}, cap {cap})", None, lines, sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order", default=str(MD / "order.json"))
    a = ap.parse_args()
    until = time.time() + DEADLINE_S

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    order = json.loads(Path(a.order).read_text(encoding="utf-8"))
    order["budget"] = a.budget
    pol, cat = mt.policy(), mt.catalog()
    try:
        stats = json.loads((mt.STATE / "sku_stats.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stats = {}
    rec = shop_record(mt.shop_names(), cat)
    accept = accept_policy(order, pol)
    cands = candidates(shops, order, pol, stats, cat, rec)
    print(f"[pick] {len(cands)} shops have the order's SKUs; accept {accept}; top "
          + ", ".join(f"{c['shop']['name']} {c['sku']} p={c['p_paid']} r={c['ratio']}" for c in cands[:3]), flush=True)

    result = {"shop": (cands[0]["shop"]["url"] if cands else (shops[0]["url"] if shops else "")), "receipt": None,
              "stars": 3, "review": f"no deal on {a.item}", "accept": accept}
    for c in cands[:pol["buyer_shops_tried"]]:
        if time.time() > until:
            break
        try:
            outcome, receipt, lines, sent = negotiate(c, order, pol, accept, until)
        except Exception as e:                                  # unreachable shop, protocol error: next shop
            outcome, receipt, lines, sent = f"error {e!r}"[:200], None, [], None
        print(f"[try] {c['shop']['name']} {c['sku']}: {outcome}", flush=True)
        with MEMORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "order": order["order"],
                                "shop": c["shop"]["name"], "item": a.item, "sku": c["sku"], "list": c["list"],
                                "budget": a.budget, "result": "deal" if receipt else outcome, "agreed": sent,
                                "receipt_price": int(receipt["price"]) if receipt else None}, ensure_ascii=False) + "\n")
        if receipt:
            price = int(receipt["price"])
            result.update(shop=c["shop"]["url"], receipt=receipt,
                          stars=5 if price <= round(c["list"] * c["ratio"]) else 4,
                          review=f"Agreed {price} for {c['name'][:40]} ({price / c['list']:.0%} of list {c['list']}).")
            break
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
