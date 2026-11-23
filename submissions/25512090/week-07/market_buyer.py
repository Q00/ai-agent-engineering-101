"""Market Buyer Agent implementing shop selection based on reviews and memory, A2A negotiation, and budget guardrails."""
import argparse
import json
import math
import re
import sys
import uuid
from pathlib import Path
import httpx

def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    try:
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=30)
        res = r.json()
    except Exception as e:
        return None, {"code": -1, "message": str(e)}
    if "error" in res:
        return None, res["error"]
    t = res["result"].get("task", res["result"])
    return t, None

def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": msg})

def state(t):
    return (t or {}).get("status", {}).get("state", "")

def last_number(t):
    text = " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None

def receipt_of(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()

    shops_path = Path(args.shops)
    shops = json.loads(shops_path.read_text(encoding="utf-8")) if shops_path.exists() else []

    memory_path = Path("buyer_memory.json")
    memory = json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else {}

    # Evaluate shops based on reviews and memory
    scored_shops = []
    for s in shops:
        name = s.get("name")
        url = s.get("url")
        reviews = s.get("reviews", [])
        
        # Calculate review score
        bad_reviews_count = sum(1 for r in reviews if r.get("stars", 5) <= 2 or any(w in r.get("review", "").lower() for w in ["overcharged", "bad", "avoid", "higher"]))
        avg_stars = sum(r.get("stars", 3) for r in reviews) / len(reviews) if reviews else 3.0
        
        # Memory score
        mem = memory.get(name, {"success": 0, "fail": 0})
        success_rate = mem["success"] / (mem["success"] + mem["fail"] + 1)
        
        # Score calculation (higher is better)
        score = avg_stars + (2.0 if bad_reviews_count == 0 else -5.0) + success_rate
        scored_shops.append((score, bad_reviews_count, s))
        print(f"[market_buyer] Shop '{name}' (url: {url}): avg_stars={avg_stars:.1f}, bad_reviews={bad_reviews_count}, score={score:.2f}")

    # Sort by score descending, but strictly avoid shops with bad reviews if alternatives exist
    good_shops = [s for score, bad, s in scored_shops if bad == 0]
    if good_shops:
        chosen = good_shops[0]
        print(f"[market_buyer] Picking shop '{chosen['name']}' with 0 bad reviews.")
    elif scored_shops:
        scored_shops.sort(key=lambda x: x[0], reverse=True)
        chosen = scored_shops[0][2]
        print(f"[market_buyer] All shops have bad reviews or none available. Picking '{chosen['name']}'.")
    else:
        print("[market_buyer] Error: No shops available.")
        sys.exit(1)

    shop_url = chosen["url"].rstrip("/")
    shop_card_url = f"{shop_url}/.well-known/agent-card.json"

    try:
        r = httpx.get(shop_card_url, timeout=10)
        card = r.json()
        iface = (card.get("supportedInterfaces") or [{}])[0]
        rpc_url = iface.get("url") or f"{shop_url}/"
    except Exception as e:
        print(f"[market_buyer] Failed to get agent card for {shop_url}: {e}")
        rpc_url = f"{shop_url}/"

    # Start negotiation
    t, err = send(rpc_url, f"I want to buy a {args.item}")
    if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
        print(f"[market_buyer] Initial negotiation failed: {err}")
        # Output result line
        result = {
            "shop": chosen["url"],
            "receipt": None,
            "stars": 1,
            "review": "Failed to initiate negotiation."
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)

    tid = t["id"]
    max_turns = 10
    final_receipt = None

    for _ in range(max_turns):
        st = state(t)
        if st == "TASK_STATE_COMPLETED":
            final_receipt = receipt_of(t)
            break
        if st in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED"):
            break

        ask = last_number(t)
        if ask is None:
            offer = args.budget
        elif ask <= args.budget:
            offer = ask
        else:
            # Never offer more than the budget
            offer = args.budget

        t, err = send(rpc_url, str(offer), tid)
        if t is None:
            break

    if state(t) == "TASK_STATE_COMPLETED":
        final_receipt = receipt_of(t)

    # Update memory
    shop_name = chosen["name"]
    if shop_name not in memory:
        memory[shop_name] = {"success": 0, "fail": 0}
    if final_receipt:
        memory[shop_name]["success"] += 1
        stars = 5
        review = "Fair price and valid receipt."
    else:
        memory[shop_name]["fail"] += 1
        stars = 3
        review = "No deal reached within budget."

    memory_path.write_text(json.dumps(memory, indent=2), encoding="utf-8")

    result = {
        "shop": chosen["url"],
        "receipt": final_receipt,
        "stars": stars,
        "review": review
    }
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
