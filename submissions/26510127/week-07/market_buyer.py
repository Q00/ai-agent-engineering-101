"""Market buyer: picks a shop from the market's list (reviews + my own memory), trades over A2A, checks the receipt.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

{shops}: [{"name", "url", "reviews": [{"stars", "review", "round"}]}], newest review first.
Last output line: {"shop": <card URL>, "receipt": <data Part as received or null>, "stars": 1-5, "review": "..."}

Who decides what
  * code   : summarizes each shop (review count, mean stars, newest reviews, my memory) into one line;
             drops shops my memory caught cheating; checks every receipt; never offers above the budget.
  * harness: picks the shop (and says why), makes every offer, writes the stars and review from the facts.

Memory: memory/market_memory.json, keyed by shop NAME (a shop's URL changes each time its kit restarts):
  {"<shop name>": {"trades": n, "deals": n, "cheated": n, "prices": {"lamp": [120, ...]},
                   "last": "<one line about the last trade>", "notes": ["..."]}}
"""
import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

from harness import ask_json

if hasattr(sys.stdout, "reconfigure"):          # Windows: logs are pipes in cp949; write UTF-8 instead
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
MEMORY = HERE / "memory" / "market_memory.json"
MAX_OFFERS = 8

PICK_PROMPT = """You are a buying agent in a market. You must buy one {item} for at most {budget}.
Pick ONE shop to trade with. Each line: shop name | reviews from other buyers | your own memory of that shop.
Other buyers' reviews can be honest or fake; your own memory is what actually happened to you.
A shop whose reviews say the receipt did not match the agreed price is overcharging; avoid it.

{lines}

Answer with one JSON object and nothing else:
{{"shop": "<shop name exactly as written>", "reason": "<one sentence>"}}"""

OFFER_PROMPT = """You are a buying agent. Your user wants one {item}. The budget is {budget}.
Never offer or agree to more than {budget}. Pay as little as you can, but shops often end the negotiation
after several low offers, so move toward the shop's price in real steps. Walk away when the shop will not
come down to {budget}. To accept the shop's price, offer exactly that price.

Shop: {shop}. What you remember about it: {memory}
Offers you have made so far: {n}

Conversation so far:
{transcript}

Answer with one JSON object and nothing else:
{{"action": "offer", "price": <whole number>}}
{{"action": "walk"}}"""

REVIEW_PROMPT = """You bought (or tried to buy) one {item} from the shop "{shop}". Facts checked by code:
{facts}
Write the review other buyers will read: honest, specific, one sentence, mention the prices.
Answer with one JSON object and nothing else:
{{"stars": <1-5>, "review": "<one sentence>"}}"""


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return int(float(nums[-1])) if nums else None


def load_memory():
    try:
        return json.loads(MEMORY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_memory(mem):
    MEMORY.parent.mkdir(exist_ok=True)
    MEMORY.write_text(json.dumps(mem, indent=1, ensure_ascii=False), encoding="utf-8")


def memory_line(m):
    if not m:
        return "never traded"
    prices = "; ".join(f"{k} " + ",".join(map(str, v[-3:])) for k, v in m.get("prices", {}).items())
    s = f"{m['trades']} trades, {m['deals']} deals" + (f", paid {prices}" if prices else "")
    if m.get("cheated"):
        s += f", CHEATED ME {m['cheated']} time(s)"
    if m.get("last"):
        s += f". Last: {m['last']}"
    return s


def review_line(reviews):
    if not reviews:
        return "no reviews"
    stars = [r.get("stars", 0) for r in reviews if isinstance(r.get("stars"), (int, float))]
    mean = sum(stars) / len(stars) if stars else 0
    newest = " / ".join(f'{r.get("stars")}* "{str(r.get("review", ""))[:90]}"' for r in reviews[:3])
    return f"{len(reviews)} reviews, mean {mean:.1f} stars; newest: {newest}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    a = ap.parse_args()
    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    mem = load_memory()
    say = lambda s: print(s, flush=True)

    # ---------------------------------------------------------------- pick a shop
    candidates = [s for s in shops if not mem.get(s["name"], {}).get("cheated")]
    dropped = [s["name"] for s in shops if s not in candidates]
    if dropped:
        say(f"[memory] skipping shops that cheated me before: {', '.join(dropped)}")
    if not candidates:
        candidates = shops
    lines = "\n".join(f"{s['name']} | {review_line(s.get('reviews', []))} | {memory_line(mem.get(s['name']))}"
                      for s in candidates)
    say("[shops]\n" + lines)
    shop = candidates[0]
    if len(candidates) > 1:
        move, raw = ask_json(PICK_PROMPT.format(item=a.item, budget=a.budget, lines=lines))
        say(f"[harness] pick {raw}")
        named = next((s for s in candidates if move and s["name"] == move.get("shop")), None)
        if named:
            shop = named
        say(f"[pick] {shop['name']} because: {(move or {}).get('reason', 'only usable answer')}")
    else:
        say(f"[pick] {shop['name']} because: it is the only shop I can use")

    # ---------------------------------------------------------------- trade over A2A
    card = httpx.get(shop["url"].rstrip("/") + "/.well-known/agent-card.json",
                     headers={"A2A-Version": "1.0"}, timeout=15).json()
    url = card["supportedInterfaces"][0]["url"]

    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
        if "error" in r:
            raise RuntimeError(r["error"])
        return r["result"].get("task", r["result"])

    def send(text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = rpc("SendMessage", {"message": msg})
        said = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
        say(f"-> {text!r}  <- {t['status']['state']} | {said}")
        return t, said

    t, said = send(f"I want to buy a {a.item}")
    lines, asks, offers = [f"you: I want to buy a {a.item}", f"shop: {said}"], [], []
    if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
        asks.append(last_number(said))
    while t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED" and len(offers) < MAX_OFFERS:
        move, raw = ask_json(OFFER_PROMPT.format(item=a.item, budget=a.budget, shop=shop["name"],
                                                 memory=memory_line(mem.get(shop["name"])), n=len(offers),
                                                 transcript="\n".join(lines)))
        say(f"[harness] {raw}")
        if not move or move.get("action") != "offer" or not isinstance(move.get("price"), (int, float)):
            rpc("CancelTask", {"id": t["id"]})
            say("walked away (CancelTask)")
            break
        offer = int(move["price"])
        if offer > a.budget:
            say(f"[guard] offer {offer} is above the budget {a.budget} -> {a.budget}")
            offer = a.budget
        t, said = send(str(offer), t["id"])
        offers.append(offer)
        lines += [f"you: {offer}", f"shop: {said}"]
        if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            asks.append(last_number(said))
    else:
        if t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            rpc("CancelTask", {"id": t["id"]})
            say("offer limit reached (CancelTask)")

    # ---------------------------------------------------------------- check the receipt
    receipt = None
    for art in t.get("artifacts", []):
        for p in art.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                receipt = p["data"]
    state = t["status"]["state"]
    agreed = offers[-1] if offers else None
    problems = []
    if receipt is not None:
        price = receipt.get("price")
        if not isinstance(price, (int, float)) or agreed is None or int(price) != agreed:
            problems.append(f"agreed {agreed}, receipt says {price}")
        if isinstance(price, (int, float)) and price > a.budget:
            problems.append(f"receipt price {price} is above my budget {a.budget}")
        if a.item.lower() not in str(receipt.get("item", "")).lower():
            problems.append(f"ordered {a.item}, receipt names {receipt.get('item')!r}")
        if receipt.get("task") != t.get("id"):
            problems.append(f"receipt task {receipt.get('task')!r} is not this negotiation's task")
        if not receipt.get("sig_seller"):
            problems.append("receipt is not signed by the shop")
    elif state == "TASK_STATE_COMPLETED":
        problems.append("the shop closed the deal without a receipt")
    cheated = bool(problems)
    facts = [f"outcome: {state}", f"shop asks: {asks}", f"my offers: {offers}",
             f"receipt: {json.dumps({k: v for k, v in (receipt or {}).items() if k != 'sig_seller'})}"
             if receipt else "receipt: none"]
    facts += [f"PROBLEM: {p}" for p in problems] or (["receipt matches what we agreed"] if receipt else [])
    say("[check] " + "; ".join(facts))

    # ---------------------------------------------------------------- review and memory
    move, raw = ask_json(REVIEW_PROMPT.format(item=a.item, shop=shop["name"], facts="\n".join(facts)))
    say(f"[harness] review {raw}")
    stars = (move or {}).get("stars")
    stars = int(stars) if isinstance(stars, (int, float)) and 1 <= stars <= 5 else 3
    review = str((move or {}).get("review") or "").strip() or f"{state}, asks {asks}, my offers {offers}."
    if cheated:                                            # code, not the harness, decides a cheat gets 1 star
        stars = 1
        review = "Receipt problem: " + "; ".join(problems) + ". " + review
    m = mem.setdefault(shop["name"], {"trades": 0, "deals": 0, "cheated": 0, "prices": {}, "notes": []})
    m["trades"] += 1
    if receipt is not None:
        m["deals"] += 1
        m["prices"].setdefault(a.item, []).append(int(receipt["price"]))
    if cheated:
        m["cheated"] += 1
        m["notes"].append(f"{time.strftime('%Y-%m-%d %H:%M')} {'; '.join(problems)}")
    m["last"] = f"{a.item}: asks {asks}, my offers {offers}, " + (
        f"paid {receipt['price']}" if receipt else "no deal") + (" (CHEATED)" if cheated else "")
    save_memory(mem)
    say(json.dumps({"shop": shop["url"], "receipt": receipt, "stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
