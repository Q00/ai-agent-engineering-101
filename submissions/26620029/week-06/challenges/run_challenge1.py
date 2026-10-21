"""Challenge 1: my seller on the whole catalog, buyers who describe instead of naming.

    python challenges/run_challenge1.py            # from submissions/26620029/week-06

Starts seller.py (catalog of 2995 items), then for each request in ch1_requests.py
plays a fixed buyer: it describes the item, then walks up an offer ladder of
0.6, 0.8, 0.95, 1.05, 1.15 x the picked item's reserve (the runner knows the truth;
the seller only sees numbers), accepting any ask within 1.15 x reserve.

A request passes when:
  - the item the seller picked (metadata sku / receipt sku) passes the request's check;
  - no ask (metadata "ask") is below that item's reserve;
  - the task completes with a receipt price >= reserve.
Writes logs/challenge-1/attempt-N/{run.log, seller.log, results.json}.
"""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
SUB = HERE.parent
sys.path[:0] = [str(SUB), str(HERE)]
from buyer import A2A, state, text_of            # noqa: E402
from ch1_requests import REQUESTS                # noqa: E402
import harness                                   # noqa: E402

LADDER = [0.6, 0.8, 0.95, 1.05, 1.15]
OUT = []


def say(line=""):
    print(line, flush=True)
    OUT.append(line)


def meta(t):
    return (t or {}).get("status", {}).get("message", {}).get("metadata") or {}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    base = SUB / "logs" / "challenge-1"
    n = 1 + max([int(p.name.split("-")[1]) for p in base.glob("attempt-*")] or [0])
    out_dir = base / f"attempt-{n}"
    out_dir.mkdir(parents=True)
    items = {it["sku"]: it for it in json.loads((SUB / "catalog.json").read_text(encoding="utf-8"))}
    port = free_port()
    seller_log = open(out_dir / "seller.log", "w")
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=SUB,
                            stdout=seller_log, stderr=subprocess.STDOUT)
    results = []
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        a2a = A2A(f"http://127.0.0.1:{port}")
        say(f"# challenge 1, attempt {n}, catalog {len(items)} items")
        for i, (text, check) in enumerate(REQUESTS, 1):
            say(f"\n## request {i}: {text!r}")
            t = a2a.send(text)
            if state(t) != "TASK_STATE_INPUT_REQUIRED":
                results.append({"request": text, "pass": False, "detail": f"opened as {state(t)}"})
                say(f"  FAIL  request {i}: opened as {state(t)}")
                continue
            sku = meta(t).get("sku")
            it = items.get(sku)
            if it is None:
                results.append({"request": text, "pass": False, "detail": f"unknown sku {sku!r} in metadata"})
                say(f"  FAIL  request {i}: unknown sku {sku!r} in metadata")
                continue
            say(f"   seller picked {sku}: {it['name']} | {it['description']} | reserve {it['reserve']}")
            asks, tid, ladder = [], t["id"], [round(f * it["reserve"]) for f in LADDER]
            budget = ladder[-1]
            for _ in range(8):
                if state(t) != "TASK_STATE_INPUT_REQUIRED":
                    break
                ask = meta(t).get("ask")
                asks.append(ask)
                offer = ask if ask is not None and ask <= budget else (ladder.pop(0) if ladder else budget)
                t = a2a.send(str(offer), tid)
            receipt = next((p.get("data") for a in (t or {}).get("artifacts", []) for p in a.get("parts", [])
                            if isinstance(p.get("data"), dict)), None)
            right = bool(it) and check(it)
            low = [a for a in asks if a is not None and a < it["reserve"]]
            price = receipt and receipt.get("price")
            ok = right and not low and state(t) == "TASK_STATE_COMPLETED" and price is not None \
                and price >= it["reserve"] and receipt.get("sku") == sku
            detail = (f"item {'ok' if right else 'WRONG'} ({sku}), asks {asks}, reserve {it['reserve']}, "
                      f"{state(t)}, receipt {price}")
            results.append({"request": text, "sku": sku, "right_item": right, "asks": asks, "reserve": it["reserve"],
                            "state": state(t), "price": price, "pass": ok})
            say(f"  {'PASS' if ok else 'FAIL'}  request {i}: {detail}")
        passed = sum(r["pass"] for r in results)
        say(f"\n{passed}/{len(results)} requests passed")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        seller_log.close()
        (out_dir / "run.log").write_text("\n".join(OUT) + "\n", encoding="utf-8")
        (out_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    calls = sum(1 for line in open(out_dir / "seller.log") if "] call " in line or line.startswith("[seller") and " call " in line)
    print(f"seller harness calls: {calls}")


if __name__ == "__main__":
    main()
