"""Runner for Challenge 2: Many shops and shopping list buyer."""
import subprocess
import time
import httpx
import os
import json
import uuid
from pathlib import Path

def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    try:
        r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=10)
        res = r.json()
        if "error" in res:
            return None, res["error"]
        t = res["result"].get("task", res["result"])
        return t, None
    except Exception as e:
        return None, {"message": str(e)}

def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return rpc(url, "SendMessage", {"message": msg})

def state(t):
    return (t or {}).get("status", {}).get("state", "")

def last_number(t):
    import re, math
    text = " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None

def receipt_price(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d["price"]
    return None

def main():
    sub = Path(__file__).resolve().parent
    log_dir = sub / "logs" / "challenge-2"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "challenge-2.log", "w")

    log_file.write("=== Starting Challenge 2: Multi-Shop Shopping List ===\n")
    log_file.flush()

    # 1. Start 3 sellers on ports 9991, 9992, 9993 with slightly different reserves/catalogs
    ports = [9991, 9992, 9993]
    sellers = []
    shop_urls = []

    for i, port in enumerate(ports):
        p_cfg = {
            "name": f"Shop {i+1}",
            "description": f"Shop {i+1} selling furniture and lamps.",
            "port": port,
            "reserve": {"lamp": 90 + i*10, "chair": 170 + i*10, "bookshelf": 250 + i*15}
        }
        cfg_path = sub / f"shop_{port}.json"
        cfg_path.write_text(json.dumps(p_cfg), encoding="utf-8")
        
        proc = subprocess.Popen(["python", "seller.py", "--config", f"shop_{port}.json", "--port", str(port)],
                                cwd=sub, stdout=log_file, stderr=subprocess.STDOUT)
        sellers.append((proc, cfg_path))
        shop_urls.append(f"http://127.0.0.1:{port}/")

    time.sleep(3) # Wait for servers to spin up

    # 2. Shopping list and total budget
    shopping_list = ["lamp", "chair", "bookshelf"]
    total_budget = 800
    spent = 0
    purchased_items = set()
    purchases = []

    log_file.write(f"Shopping list: {shopping_list}, Total Budget: {total_budget}\n")
    log_file.flush()

    # 3. Buyer negotiates with shops for each item on the shopping list
    for item in shopping_list:
        best_deal = None
        best_price = float('inf')
        best_shop_url = None
        
        for url in shop_urls:
            # Check card
            try:
                card_res = httpx.get(f"{url}.well-known/agent-card.json", timeout=3)
                if card_res.status_code != 200:
                    continue
                iface = card_res.json()["supportedInterfaces"][0]
                rpc_url = iface["url"]
            except Exception:
                continue

            # Start negotiation
            t, err = send(rpc_url, f"I want to buy a {item}")
            if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
                continue
            
            tid = t["id"]
            ask = last_number(t)
            
            # Negotiate turn
            max_rounds = 5
            deal_reached = False
            final_price = None
            
            for _ in range(max_rounds):
                if ask is None:
                    offer = 200
                else:
                    offer = ask
                
                t, err = send(rpc_url, str(offer), tid)
                if t is None:
                    break
                
                st = state(t)
                if st == "TASK_STATE_COMPLETED":
                    p = receipt_price(t)
                    if p is not None:
                        final_price = p
                        deal_reached = True
                        break
                elif st == "TASK_STATE_INPUT_REQUIRED":
                    ask = last_number(t)
                else:
                    break

            if deal_reached and final_price < best_price:
                best_price = final_price
                best_shop_url = url
                best_deal = (item, final_price)

        if best_deal and (spent + best_price <= total_budget) and (best_deal[0] not in purchased_items):
            purchased_items.add(best_deal[0])
            spent += best_price
            purchases.append(best_deal)
            log_file.write(f"BOUGHT {best_deal[0]} for {best_deal[1]} from {best_shop_url}. Total spent: {spent}\n")
        else:
            log_file.write(f"FAILED to buy {item} within budget.\n")
        log_file.flush()

    log_file.write(f"Final Purchases: {purchases}, Total Spent: {spent} / {total_budget}\n")
    log_file.flush()

    # Cleanup
    log_file.close()
    for proc, cfg_path in sellers:
        proc.terminate()
        try:
            cfg_path.unlink()
        except Exception:
            pass

    print(f"Challenge 2 completed. Purchases: {purchases}, Total spent: {spent}")

if __name__ == "__main__":
    main()
