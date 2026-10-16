"""Challenge 1 driver: a scripted buyer describes items to the seller on the 3000-item catalog.
Writes logs/challenge-1/.

    python challenge1.py

Each request is a description, never an item name. The driver offers a fixed ladder (50, 65, 80, 90,
100 percent of the first ask) and checks, from the catalog itself:
  - right item: the item the seller chose satisfies the request's checker (kind, material, size ...)
  - reserve held: every ask the seller made (read from message metadata) and the sale price are at or
    above that item's reserve
Two extra requests check the unclear-input path: one unrelated, one vague. They pass when the seller
answers with a question or "not sure" instead of quoting a price for some item.
"""
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "challenge-1"
PORT = 9301
URL = f"http://127.0.0.1:{PORT}/"
WOOD = {"oak", "pine", "walnut", "birch", "teak", "beech"}

REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     lambda i: i["kind"] in ("bookshelf", "wall shelf") and i["attrs"]["material"] in WOOD
     and i["attrs"].get("height_cm", 999) <= 80 and 25 <= i["attrs"].get("holds_books", 0) <= 60),
    ("an office chair with wheels for long work days",
     lambda i: i["kind"] == "office chair" and i["attrs"].get("wheels")),
    ("a dimmable lamp for my bedside table",
     lambda i: "lamp" in i["kind"] and i["attrs"].get("dimmable")),
    ("a dining table that seats 6 people",
     lambda i: i["kind"] == "dining table" and i["attrs"].get("seats") == 6),
    ("a leather sofa with three seats",
     lambda i: i["kind"] in ("sofa", "sofa bed") and i["attrs"]["material"] == "leather" and i["attrs"].get("seats") == 3),
]
UNCLEAR = ["what's the weather like today?", "I need something nice"]


def rpc(method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    return httpx.post(URL, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()


def send(text, task_id=None, log=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    r = rpc("SendMessage", {"message": msg})
    t = r.get("result", {}).get("task", r.get("result", {}))
    st = t.get("status", {})
    words = " ".join(p.get("text", "") for p in st.get("message", {}).get("parts", []))
    ask = (st.get("message", {}).get("metadata") or {}).get("ask")
    log(f"  -> {text!r}\n  <- {st.get('state')} | ask={ask} | {words[:220]}")
    return t, st.get("state"), ask, words


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = {i["id"]: i for i in json.load(open(HERE / "catalog.json"))["items"]}
    seller_log = open(OUT / "seller.log", "w")
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(PORT)], cwd=HERE,
                            stdout=seller_log, stderr=subprocess.STDOUT)
    lines, results = [], []
    log = lambda s: (lines.append(s), print(s))
    try:
        for _ in range(60):
            try:
                httpx.get(URL + ".well-known/agent-card.json", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        for text, check in REQUESTS:
            log(f"\n### {text}")
            t, st, ask, words = send(text, log=log)
            m = re.search(r"\((P-\d{5}|lamp|chair|bookshelf):", words)
            item = catalog.get(m.group(1)) if m else None
            asks = [ask] if ask is not None else []
            if st == "TASK_STATE_INPUT_REQUIRED" and item and ask:
                first = ask
                for pct in (50, 65, 80, 90, 100):
                    t, st, ask, words = send(str(first * pct // 100), t["id"], log=log)
                    if ask is not None:
                        asks.append(ask)
                    if st != "TASK_STATE_INPUT_REQUIRED":
                        break
                if st == "TASK_STATE_INPUT_REQUIRED":
                    rpc("CancelTask", {"id": t["id"]})
            price = next((p["data"]["price"] for a in t.get("artifacts", []) for p in a.get("parts", [])
                          if isinstance(p.get("data"), dict) and "price" in p["data"]), None)
            r = {"request": text, "item": item and item["id"], "name": item and item["name"],
                 "description": item and item["description"], "reserve": item and item["reserve"],
                 "asks": asks, "final_state": st, "price": price,
                 "right_item": bool(item and check(item)),
                 "reserve_held": bool(item) and all(a >= item["reserve"] for a in asks)
                 and (price is None or price >= item["reserve"])}
            r["passed"] = r["right_item"] and r["reserve_held"]
            results.append(r)
            log(f"  => item {r['item']} {r['name']!r} reserve {r['reserve']}, asks {asks}, {st}, price {price}, "
                f"right item {r['right_item']}, reserve held {r['reserve_held']}")
        for text in UNCLEAR:
            log(f"\n### (unclear) {text}")
            t, st, ask, words = send(text, log=log)
            ok = st == "TASK_STATE_INPUT_REQUIRED" and ask is None
            results.append({"request": text, "unclear": True, "state": st, "reply": words, "passed": ok})
            if st == "TASK_STATE_INPUT_REQUIRED":
                rpc("CancelTask", {"id": t["id"]})
            log(f"  => asked back instead of quoting a price: {ok}")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    summary = {"passed": all(r["passed"] for r in results),
               "requests": len(results), "passed_requests": sum(r["passed"] for r in results), "results": results}
    (OUT / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    (OUT / "driver.log").write_text("\n".join(lines) + "\n")
    print(f"\nchallenge 1: {summary['passed_requests']}/{summary['requests']} passed")


if __name__ == "__main__":
    main()
