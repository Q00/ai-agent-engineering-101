"""Challenge 1 driver: my warehouse seller (3000 items) against a fixed buyer that describes what it wants.

    python challenge1.py        # writes logs/challenge-1/run.log, seller.log, result.json

The fixed buyer has no model. For each request in challenges/requests-1.json it sends the description, reads the
offered item (metadata "item") and the ask (metadata "ask", checked against the last number in the text), then walks
a ladder of offers up to its budget and accepts any ask within the budget, like the trade test's kit buyer.
  budget "deal" = 1.25 x the median reserve of the items that fit the spec
  budget "hold" = 0.7 x the lowest reserve of the items that fit (no deal is possible)
A request passes when the offered item fits the spec (spec null: nothing may be offered), no ask is below that
item's reserve, and a receipt, if any, is for that item at or above its reserve.
"""
import json
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

import catalog
from spec_check import satisfies

HERE = Path(__file__).parent
OUT = HERE / "logs" / "challenge-1"
SHOP = HERE / "shops" / "warehouse.json"
PARALLEL = 3
MAX_MESSAGES = 8
LADDER = [0.5, 0.65, 0.8, 0.9, 1.0]
LOCK = threading.Lock()
LOG = []


def log(lines):
    with LOCK:
        for line in lines if isinstance(lines, list) else [lines]:
            print(line, flush=True)
            LOG.append(line)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def send(url, text, out, task_id=None, method="SendMessage"):
    if method == "CancelTask":
        params = {"id": task_id}
        out.append(f"    -> CancelTask {task_id[:8]}")
    else:
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        params = {"message": msg}
        out.append(f"    -> {json.dumps(text)}" + (f" taskId={task_id[:8]}" if task_id else ""))
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=300).json()
    if "error" in r:
        out.append(f"    <- error {r['error'].get('code')} {r['error'].get('message', '')[:100]}")
        return None
    t = r["result"].get("task", r["result"])
    msg = t.get("status", {}).get("message", {})
    text = " ".join(p.get("text", "") for p in msg.get("parts", []))
    out.append(f"    <- {t['status']['state']} | {text[:230]}" + (f" | metadata {msg.get('metadata')}" if msg.get("metadata") else ""))
    for a in t.get("artifacts", []):
        out.append(f"       artifact {a.get('name')} {json.dumps([p.get('data', p.get('text')) for p in a.get('parts', [])])}")
    return t


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def ask_of(t, out):
    msg = (t or {}).get("status", {}).get("message", {})
    meta = (msg.get("metadata") or {}).get("ask")
    nums = re.findall(r"\d+(?:\.\d+)?", " ".join(p.get("text", "") for p in msg.get("parts", [])))
    last = int(float(nums[-1])) if nums else None
    if meta is not None and last is not None and int(meta) != last:
        out.append(f"    !! metadata ask {meta} != last number {last}")
    return int(meta) if meta is not None else last


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def play(url, r, items):
    out = [f"\n### {r['id']}: {r['want']!r}, budget {r['budget_n']} ({r['budget']})"]
    res = {"id": r["id"], "want": r["want"], "budget": r["budget_n"], "asks": [], "offers": []}
    t = send(url, r["want"], out)
    res["opened"] = state(t)
    if r["spec"] is None:
        res["pass"] = state(t) != "TASK_STATE_INPUT_REQUIRED"
        res["detail"] = f"{state(t)}; nothing should be offered"
        if not res["pass"]:
            send(url, "", out, t["id"], "CancelTask")
        out.append(f"  {'PASS' if res['pass'] else 'FAIL'}  {r['id']}: {res['detail']}")
        return res, out
    if state(t) != "TASK_STATE_INPUT_REQUIRED":
        res.update({"pass": False, "detail": f"not opened: {state(t)}"})
        out.append(f"  FAIL  {r['id']}: {res['detail']}")
        return res, out
    meta = (t["status"]["message"].get("metadata") or {})
    item = items.get(meta.get("item"))
    if item is None:
        res.update({"pass": False, "detail": f"offered item {meta.get('item')!r} is not in the catalog"})
        out.append(f"  FAIL  {r['id']}: {res['detail']}")
        return res, out
    fits, why = satisfies(item, r["spec"])
    res.update({"item": item["id"], "reserve": item["reserve"], "fits": fits, "why": why})
    out.append(f"    item {item['id']} reserve {item['reserve']}: {why}")
    tid, ladder = t["id"], [round(f * r["budget_n"]) for f in LADDER]
    for _ in range(MAX_MESSAGES):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask = ask_of(t, out)
        if ask is not None:
            res["asks"].append(ask)
        offer = ask if ask is not None and ask <= r["budget_n"] else (ladder.pop(0) if ladder else r["budget_n"])
        res["offers"].append(offer)
        t = send(url, str(offer), out, tid)
        if t is None:
            break
    if state(t) == "TASK_STATE_INPUT_REQUIRED":
        res["asks"].append(ask_of(t, out))
        send(url, "", out, tid, "CancelTask")
    rc = receipt(t)
    res["state"], res["receipt"] = state(t), rc
    low = [a for a in res["asks"] if a is not None and a < item["reserve"]]
    sold_ok = rc is None or (rc.get("item_id") == item["id"] and rc["price"] >= item["reserve"])
    res["pass"] = fits and not low and sold_ok
    res["detail"] = (f"{item['id']} {'fits' if fits else 'WRONG ITEM (' + why + ')'}, reserve {item['reserve']}, "
                     f"asks {res['asks']}, {state(t) or 'error'}" + (f", sold at {rc['price']}" if rc else "")
                     + (f", asks below reserve {low}" if low else ""))
    out.append(f"  {'PASS' if res['pass'] else 'FAIL'}  {r['id']}: {res['detail']}")
    return res, out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(SHOP.read_text(encoding="utf-8"))
    items = {it["id"]: it for it in catalog.load_shop(cfg)}
    reqs = json.loads((HERE / "challenges" / "requests-1.json").read_text(encoding="utf-8"))
    for r in reqs:
        fit = [it["reserve"] for it in items.values() if r["spec"] and satisfies(it, r["spec"])[0]]
        r["budget_n"] = (round(1.25 * statistics.median(fit)) if r["budget"] == "deal" else
                         round(0.7 * min(fit)) if r["budget"] == "hold" else r["budget"])
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    log([f"# challenge 1, {started}: warehouse seller, {len(items)} items, catalog file "
         f"{(HERE / cfg['catalog']).stat().st_size} bytes", f"seller: python seller.py --config shops/warehouse.json --port {port}"])
    sl = open(OUT / "seller.log", "w")
    proc = subprocess.Popen([sys.executable, "seller.py", "--config", "shops/warehouse.json", "--port", str(port)],
                            cwd=HERE, stdout=sl, stderr=subprocess.STDOUT)
    t0 = time.time()
    try:
        for _ in range(120):
            try:
                if httpx.get(url + ".well-known/agent-card.json", timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        results = []
        with ThreadPoolExecutor(PARALLEL) as ex:
            for res, out in ex.map(lambda r: play(url, r, items), reqs):
                log(out)
                results.append(res)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        sl.close()
    seller_log = (OUT / "seller.log").read_text(encoding="utf-8")
    sizes = [int(x) for x in re.findall(r"in=(\d+)ch", seller_log)]
    passed = all(r["pass"] for r in results)
    deals = [r for r in results if r.get("receipt")]
    summary = {"started": started, "seconds": round(time.time() - t0), "requests": len(results),
               "passed": sum(r["pass"] for r in results), "deals": len(deals), "harness_calls": len(sizes),
               "largest_prompt_chars": max(sizes or [0]), "catalog_bytes": (HERE / cfg["catalog"]).stat().st_size,
               "guard_lines": seller_log.count("[guard]"), "all_pass": passed}
    log(["", f"{summary['passed']}/{len(results)} passed -> {'PASS' if passed else 'FAIL'}; deals {len(deals)}; "
             f"{len(sizes)} harness calls, largest prompt {summary['largest_prompt_chars']} chars "
             f"(catalog {summary['catalog_bytes']} bytes); guard lines {summary['guard_lines']}; {summary['seconds']} s"])
    (OUT / "result.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1) + "\n", encoding="utf-8")
    (OUT / "run.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
