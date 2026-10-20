"""Challenge 1 check: buyers describe an item instead of naming it.

    python challenge1.py 2>&1 | tee logs/challenge-1/run.txt

Starts seller.py, sends each request below, and plays a fixed buyer: offers at
50%, 70% and 85% of the first ask, then the current ask, so every run ends in a deal
and the receipt says which item was sold. A run passes when the sold item is one of
the expected kinds and no ask and no deal was below that item's reserve. Whether the
sizes fit (40 books, desk height) is printed for a human to judge.
"""
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).parent
PORT = 9871
URL = f"http://127.0.0.1:{PORT}/"
HEADERS = {"A2A-Version": "1.0"}
CASES = [
    ("something wooden to keep about 40 books, not taller than my desk", {"bookcase", "cube organizer", "wall shelf", "bookshelf"}),
    ("a comfy couch that seats three people", {"sofa"}),
    ("a light to read by next to my armchair, standing on the floor", {"floor lamp"}),
    ("a chair I can sit in all day while coding", {"office chair"}),
    ("somewhere to put my shoes by the front door", {"shoe cabinet"}),
    ("I want to buy a lamp", {"lamp"}),
]
CATALOG = {i["id"]: i for i in json.loads((HERE / "catalog.json").read_text())}


def send(text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    body = {"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}}
    r = httpx.post(URL, json=body, headers=HEADERS, timeout=200).json()
    return r["result"]["task"]


def said(task):
    return " ".join(p.get("text", "") for p in task["status"].get("message", {}).get("parts", []))


def run(request, kinds):
    print(f"\n### {request}")
    task = send(request)
    asks = []
    for step in range(6):
        state, text = task["status"]["state"], said(task)
        print(f"  {state}: {text}")
        if state != "TASK_STATE_INPUT_REQUIRED":
            break
        ask = int(re.findall(r"\d+", text)[-1])
        asks.append(ask)
        ladder = [int(asks[0] * f) for f in (0.5, 0.7, 0.85)]
        offer = ladder[step] if step < len(ladder) else ask
        print(f"  buyer offers {offer}")
        task = send(str(offer), task["id"])
    receipt = next((p["data"] for a in task.get("artifacts", []) for p in a["parts"] if "data" in p), None)
    if receipt is None:
        print("  FAIL no deal")
        return False
    item = CATALOG[receipt["id"]]
    ok_kind = item["kind"] in kinds
    ok_reserve = receipt["price"] >= item["reserve"] and all(a >= item["reserve"] for a in asks)
    print(f"  sold {item['id']} ({item['kind']}, reserve {item['reserve']}) at {receipt['price']}; asks {asks}")
    print(f"  item: {item['description']}")
    print(f"  {'PASS' if ok_kind and ok_reserve else 'FAIL'} kind={'ok' if ok_kind else 'wrong'} reserve={'ok' if ok_reserve else 'BROKEN'}")
    return ok_kind and ok_reserve


def main():
    log = open(HERE / "logs" / "challenge-1" / "seller.log", "w")
    seller = subprocess.Popen([sys.executable, "seller.py", "--port", str(PORT)], cwd=HERE, stdout=log, stderr=log)
    try:
        for _ in range(50):
            try:
                httpx.get(URL + ".well-known/agent-card.json", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        start = time.time()
        results = [run(r, k) for r, k in CASES]
        print(f"\n{sum(results)}/{len(results)} passed in {time.time() - start:.0f} s")
    finally:
        seller.terminate()


if __name__ == "__main__":
    main()
