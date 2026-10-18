"""Week 06 challenge 1 — challenge1_test.py: test the large-catalog seller over A2A.

    python challenge1_test.py                 # all cases in challenge1_cases.json
    python challenge1_test.py c07 c12         # only cases whose id starts with these

No kit exists for challenge 1, so this follows the trade test's shape. It starts
catalog_seller.py on a free port and plays a rule-based buyer for each case:
  - sends the description; answers the shop's questions from the case's prepared answers
  - when an item is offered, walks an offer ladder up to its budget and takes any ask within it
and checks, from the test's own ground truth (not the seller's reading of the request):
  right_item   the offered item meets the case's expected conditions (or no item for no_item cases)
  reserve      no ask and no sale below that item's reserve (looked up in catalog.json)
  receipt      a deal's receipt names the offered item_id, at a price within reserve..budget
  deal         for "deal": false cases, no deal happens
Writes logs/challenge-1/ (one log per case, the seller's output, challenge1-report.json).
"""
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "challenge-1")
LADDER = [0.5, 0.65, 0.8, 0.9, 1.0]     # offers as fractions of the budget
MAX_MESSAGES = 10
ITEM_RX = re.compile(r"\[([A-Z]{2}-\d{4})\]")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rpc(url, method, params, log):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
    if "error" in r:
        log(f"    <- error {r['error'].get('code')} {r['error'].get('message', '')[:140]}")
        return None
    return r["result"].get("task", r["result"])


def send(url, text, task_id, log):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"    -> {json.dumps(text)}" + (f" taskId={task_id[:8]}" if task_id else ""))
    t = rpc(url, "SendMessage", {"message": msg}, log)
    if t:
        log(f"    <- {state(t)} | {text_of(t)}")
        for a in t.get("artifacts", []):
            log(f"       artifact {a.get('name')} {json.dumps([p.get('data', p.get('text')) for p in a.get('parts', [])])}")
    return t


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def meets(item, e):
    for f in ("category", "subtype", "material", "color", "style"):
        if f in e and item.get(f) not in e[f]:
            return False
    for f, v in e.get("min", {}).items():
        if item.get(f) is None or item[f] < v:
            return False
    for f, v in e.get("max", {}).items():
        if item.get(f) is None or item[f] > v:
            return False
    return True


def run_case(url, case, catalog, log):
    e = case["expect"]
    answers = list(case["answers"])
    t = send(url, case["request"], None, log)
    if t is None:
        return {"id": case["id"], "pass": False, "detail": "no task"}
    tid, item, asks, questions = t["id"], None, [], 0
    ladder = [round(case["budget"] * f) for f in LADDER]
    for _ in range(MAX_MESSAGES):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        msg = text_of(t)
        m = ITEM_RX.search(msg)
        if item is None and m:                           # the shop offered an item
            item = catalog.get(m.group(1))
            log(f"    [test] offered {m.group(1)}: {item['name'] if item else 'NOT IN CATALOG'}"
                + (f" (reserve {item['reserve']})" if item else ""))
        if item is None:                                 # still a question
            questions += 1
            reply = answers.pop(0) if answers else "I don't mind, you choose."
            t = send(url, reply, tid, log)
            continue
        ask = last_number(msg)
        if ask is not None:
            asks.append(ask)
        offer = ask if ask is not None and ask <= case["budget"] else (ladder.pop(0) if ladder else case["budget"])
        t = send(url, str(offer), tid, log)
    if t is not None and state(t) == "TASK_STATE_INPUT_REQUIRED" and item is not None:
        n = last_number(text_of(t))
        if n is not None:
            asks.append(n)
    if t is not None and state(t) == "TASK_STATE_INPUT_REQUIRED":
        rpc(url, "CancelTask", {"id": tid}, log)
        log("    -> CancelTask (test buyer leaves)")

    rc = receipt(t)
    checks = {}
    if e.get("no_item"):
        checks["right_item"] = item is None and rc is None
        detail = f"item offered: {item['id'] if item else 'none'}, final {state(t) or 'error'}, {questions} question(s)"
    else:
        checks["right_item"] = item is not None and meets(item, e)
        low = [a for a in asks if item and a < item["reserve"]]
        checks["reserve"] = item is not None and not low and (rc is None or rc["price"] >= item["reserve"])
        if rc is not None:
            checks["receipt"] = (rc.get("item_id") == item["id"] and item["reserve"] <= rc["price"] <= case["budget"])
        if e.get("deal") is False:
            checks["deal"] = rc is None
        detail = (f"item {item['id'] if item else 'none'}"
                  + (f" ({item['material']} {item['subtype']} {item['category']}, reserve {item['reserve']})" if item else "")
                  + f", asks {asks}, {questions} question(s), "
                  + (f"deal at {rc['price']}" if rc else f"no deal ({state(t) or 'error'})"))
    return {"id": case["id"], "group": case["group"], "pass": all(checks.values()), "checks": checks, "detail": detail}


def main():
    cases = json.load(open(os.path.join(HERE, "challenge1_cases.json"), encoding="utf-8"))
    if sys.argv[1:]:
        cases = [c for c in cases if any(c["id"].startswith(p) for p in sys.argv[1:])]
    catalog = {it["id"]: it for it in json.load(open(os.path.join(HERE, "catalog.json"), encoding="utf-8"))}
    os.makedirs(OUT, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seller_out = open(os.path.join(OUT, "seller.log"), "w", encoding="utf-8")
    seller = subprocess.Popen([sys.executable, "catalog_seller.py", "--port", str(port)], cwd=HERE,
                              stdout=seller_out, stderr=subprocess.STDOUT)
    summary_lines = [f"# challenge 1 test, {started}, seller catalog_seller.py on :{port}"]
    results = []
    try:
        for _ in range(60):
            try:
                if httpx.get(url + ".well-known/agent-card.json", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(1)
        for case in cases:
            lines = []
            def log(s):
                print(s, flush=True)
                lines.append(s)
            log(f"\n### {case['id']} [{case['group']}] budget {case['budget']}")
            t0 = time.time()
            try:
                r = run_case(url, case, catalog, log)
            except Exception as ex:
                r = {"id": case["id"], "group": case["group"], "pass": False, "checks": {}, "detail": f"error: {ex}"}
            r["seconds"] = round(time.time() - t0)
            verdict = f"  {'PASS' if r['pass'] else 'FAIL'}  {case['id']}: {r['detail']} {r.get('checks', {})} ({r['seconds']} s)"
            log(verdict)
            summary_lines.append(verdict)
            results.append(r)
            with open(os.path.join(OUT, f"{case['id']}.log"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
    finally:
        seller.terminate()
        seller_out.close()
    passed = sum(r["pass"] for r in results)
    summary_lines.append(f"\n{passed}/{len(results)} passed")
    print(summary_lines[-1])
    with open(os.path.join(OUT, "challenge1-test.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines) + "\n")
    with open(os.path.join(OUT, "challenge1-report.json"), "w", encoding="utf-8") as f:
        json.dump({"started": started, "seller": "catalog_seller.py", "passed": passed, "total": len(results),
                   "results": results}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
