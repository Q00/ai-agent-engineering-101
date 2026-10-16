"""Cross-trade readiness: our agents against counterparts built differently from ours.
Writes logs/robustness/.

    python robustness.py

Part B, our buyer (buyer.py) against four odd sellers defined here (rule-based, no model):
  legacy        A2A v0.3 style: no supportedInterfaces (card has "url"), only message/send and
                tasks/cancel, lowercase states ("input-required"), parts with "kind", "$130" prices
  message_only  answers with a bare Message, never a Task; the receipt comes as a data part
  rpc_error     answers the second SendMessage with a JSON-RPC error
  big_numbers   prices with thousands separators ("$1,450"), budget 1,300
Each passes when buyer.py exits on its own without a crash and never pays more than its budget.

Part S, our seller (seller.py) against odd buyers:
  greeting      "Hi, I'd like to purchase a lamp" still opens on the lamp (reserve 100)
  data_offer    the offer arrives as a data part {"price": 125}
  formats       "$105" and "110 dollars" are read as offers
  concurrent    a second buyer is answered while the first one's harness call is running
  hold_lapse    a buyer who goes quiet loses the hold (SELLER_HOLD_IDLE=5), the next buyer gets the item
  no_harness    with the harness missing (HARNESS_CMD=/nonexistent) the seller still answers, never below reserve
"""
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "robustness"
LINES = []


def log(s=""):
    LINES.append(s)
    print(s)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(url, path="/.well-known/agent-card.json"):
    for _ in range(80):
        try:
            if httpx.get(url + path, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.25)
    return False


# ------------------------------------------------------------------ part B: odd sellers
def odd_seller(style, port, floor, record):
    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    base = f"http://127.0.0.1:{port}"
    state = {}            # task or context id -> {"ask", "rounds"}
    money = (lambda n: f"${n:,}") if style in ("legacy", "big_numbers") else str

    if style == "legacy":
        card = {"name": "Legacy Shop", "description": "v0.3 style", "url": base + "/", "version": "0.3.0",
                "skills": [{"id": "sell", "name": "Sell", "description": "Sells a lamp."}]}
    else:
        card = {"name": f"{style} shop", "description": "odd seller", "version": "1.0.0",
                "supportedInterfaces": [{"url": base + "/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
                "skills": [{"id": "sell", "name": "Sell", "description": "Sells things."}]}

    def text_part(t):
        return {"kind": "text", "text": t} if style == "legacy" else {"text": t}

    def task(tid, ctx, st, text, artifact=None):
        if style == "legacy":
            st = st.lower().replace("task_state_", "").replace("_", "-")
        t = {"id": tid, "contextId": ctx, "status": {"state": st, "message": {"role": "agent", "parts": [text_part(text)]}}}
        if style == "legacy":
            t["kind"] = "task"
        if artifact:
            t["artifacts"] = [{"name": "receipt", "parts": [({"kind": "data"} if style == "legacy" else {}) | {"data": artifact}]}]
        return {"jsonrpc": "2.0", "id": "x", "result": t if style == "legacy" else {"task": t}}

    def message(ctx, text, receipt=None):
        parts = [{"text": text}] + ([{"data": receipt}] if receipt else [])
        return {"jsonrpc": "2.0", "id": "x", "result": {"message": {"messageId": str(uuid.uuid4()), "contextId": ctx,
                                                                   "role": "ROLE_AGENT", "parts": parts}}}

    async def card_route(request):
        return JSONResponse(card)

    async def rpc(request):
        body = await request.json()
        record["methods"].append(body["method"])
        if request.headers.get("A2A-Version") != "1.0":
            record["no_version_header"] += 1
        send_name = "message/send" if style == "legacy" else "SendMessage"
        cancel_name = "tasks/cancel" if style == "legacy" else "CancelTask"
        if body["method"] == cancel_name:
            record["canceled"] += 1
            return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": {"id": body["params"]["id"], "status": {"state": "canceled"}}})
        if body["method"] != send_name:
            return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "Method not found"}})
        msg = body["params"]["message"]
        text = " ".join(p.get("text", "") for p in msg.get("parts", []))
        key = msg.get("taskId") or msg.get("contextId")
        if style == "rpc_error" and key in state:
            record["errors_sent"] += 1
            return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32603, "message": "internal error"}})
        if key not in state:
            key = str(uuid.uuid4())
            state[key] = {"ask": math.ceil(floor * 1.3), "rounds": 0}
            words = f"Welcome. My price for it is {money(state[key]['ask'])}."
        else:
            nums = [int(n.replace(",", "")) for n in re.findall(r"\d{1,3}(?:,\d{3})+|\d+", text)]
            offer = nums[-1] if nums else 0
            record["offers"].append(offer)
            if offer >= floor:
                record["deals"].append(offer)
                receipt = {"item": "thing", "price": offer}
                del state[key]
                if style == "message_only":
                    return JSONResponse(message(key, f"Deal at {offer}.", receipt))
                return JSONResponse(task(key, key, "TASK_STATE_COMPLETED", f"Deal at {money(offer)}.", receipt))
            s = state[key]
            s["rounds"] += 1
            if s["rounds"] >= 6:
                del state[key]
                return JSONResponse(task(key, key, "TASK_STATE_REJECTED", "No deal."))
            s["ask"] = max(floor, (offer + s["ask"]) // 2)
            words = f"Too low. I can do {money(s['ask'])}."
        if style == "message_only":
            return JSONResponse(message(key, words))
        return JSONResponse(task(key, key, "TASK_STATE_INPUT_REQUIRED", words))

    app = Starlette(routes=[Route("/.well-known/agent-card.json", card_route), Route("/", rpc, methods=["POST"])])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def part_b():
    cases = [("legacy", 100, "lamp", 120), ("message_only", 100, "lamp", 120),
             ("rpc_error", 100, "lamp", 120), ("big_numbers", 1100, "bookshelf", 1300)]
    results = []
    for style, floor, item, budget in cases:
        port = free_port()
        record = {"methods": [], "offers": [], "deals": [], "canceled": 0, "errors_sent": 0, "no_version_header": 0}
        server = odd_seller(style, port, floor, record)
        wait_up(f"http://127.0.0.1:{port}")
        log(f"\n### B/{style}: our buyer, {item}, budget {budget}, seller floor {floor}")
        out = OUT / f"buyer-{style}.log"
        p = subprocess.run([sys.executable, "buyer.py", "--url", f"http://127.0.0.1:{port}", "--item", item,
                            "--budget", str(budget)], cwd=HERE, capture_output=True, text=True, timeout=300)
        out.write_text(p.stdout + p.stderr)
        server.should_exit = True
        time.sleep(0.5)
        tail = [l for l in p.stdout.splitlines() if re.search(r"bought|walked|ended in|could not|error|switching|read the asking", l)]
        crashed = re.search(r"\[buyer\] error:", p.stdout)     # buyer.py's crash line, not "seller error:"
        ok = p.returncode == 0 and not crashed and all(d <= budget for d in record["deals"])
        if style in ("legacy", "message_only", "big_numbers"):
            ok = ok and len(record["deals"]) == 1          # a deal within budget was possible
        log(f"  seller saw methods {sorted(set(record['methods']))}, offers {record['offers']}, deals {record['deals']}, "
            f"canceled {record['canceled']}, posts without A2A-Version {record['no_version_header']}")
        for l in tail[-3:]:
            log(f"  buyer: {l[11:200]}")
        log(f"  => exit {p.returncode}, passed {ok}")
        results.append({"case": f"B/{style}", "exit": p.returncode, **record, "passed": ok})
    return results


# ------------------------------------------------------------------ part S: odd buyers
class Client:
    def __init__(self, url):
        self.url = url

    def send(self, parts, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": parts}
        if task_id:
            msg["taskId"] = task_id
        body = {"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}}
        r = httpx.post(self.url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
        if "error" in r:
            return {"error": r["error"]}
        t = r["result"].get("task", r["result"])
        st = t.get("status", {})
        t["_state"] = st.get("state")
        t["_ask"] = (st.get("message", {}).get("metadata") or {}).get("ask")
        t["_text"] = " ".join(p.get("text", "") for p in st.get("message", {}).get("parts", []))
        return t

    def text(self, s, task_id=None):
        return self.send([{"text": s}], task_id)

    def cancel(self, task_id):
        httpx.post(self.url, json={"jsonrpc": "2.0", "id": "2", "method": "CancelTask", "params": {"id": task_id}},
                   headers={"A2A-Version": "1.0"}, timeout=30)


def start_seller(name, env_extra=None):
    port = free_port()
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=HERE, env=env,
                            stdout=open(OUT / f"seller-{name}.log", "w"), stderr=subprocess.STDOUT)
    wait_up(f"http://127.0.0.1:{port}")
    return proc, Client(f"http://127.0.0.1:{port}/")


def show(t):
    return f"{t.get('_state')} ask={t.get('_ask')} | {t.get('_text', t.get('error'))!s:.150}"


def part_s():
    results = []

    def case(name, ok, detail):
        log(f"  => {name}: passed {ok} ({detail})")
        results.append({"case": f"S/{name}", "passed": bool(ok), "detail": detail})

    proc, c = start_seller("main")
    try:
        log("\n### S/greeting")
        t = c.text("Hi, I'd like to purchase a lamp")
        log(f"  <- {show(t)}")
        case("greeting", t["_state"] == "TASK_STATE_INPUT_REQUIRED" and (t["_ask"] or 0) >= 100
             and t["_text"].startswith("lamp:"), f"opened on {t['_text'][:20]!r} at {t['_ask']}")
        c.cancel(t["id"])

        log("\n### S/data_offer: offer sent as a data part")
        t = c.text("I want to buy a lamp")
        t2 = c.send([{"data": {"price": 125}}], t["id"])
        log(f"  <- {show(t2)}")
        asks = [a for a in (t["_ask"], t2.get("_ask")) if a is not None]
        read = re.findall(r"<- '(\d+)'", (OUT / "seller-main.log").read_text())
        case("data_offer", "error" not in t2 and all(a >= 100 for a in asks) and "125" in read,
             f"seller took the data part as offer {read[-1] if read else None}, now {t2.get('_state')}")
        if t2.get("_state") == "TASK_STATE_INPUT_REQUIRED":
            c.cancel(t["id"])

        log("\n### S/formats: \"$105\" then \"110 dollars\"")
        t = c.text("I want to buy a chair")
        seen = []
        for s in ("$105", "110 dollars"):
            if t.get("_state") != "TASK_STATE_INPUT_REQUIRED":
                break
            t = c.text(s, t["id"])
            log(f"  -> {s!r}\n  <- {show(t)}")
            seen.append(t.get("_state"))
        lg = (OUT / "seller-main.log").read_text()
        no_free = "harness read the offer" not in lg.split("<- '$105'")[-1][:2000]
        case("formats", "error" not in t and no_free and all(s for s in seen),
             f"read as plain offers without the harness: {no_free}; states {seen}")
        if t.get("_state") == "TASK_STATE_INPUT_REQUIRED":
            c.cancel(t["id"])

        log("\n### S/concurrent: buyer B is answered while buyer A's harness call runs")
        a = c.text("I want to buy a bookshelf")
        def a_offer():
            t0 = time.time(); r = c.text("150", a["id"]); return time.time() - t0, r
        def b_open():
            time.sleep(0.3); t0 = time.time(); r = c.text("I want to buy a lamp"); return time.time() - t0, r
        with ThreadPoolExecutor(2) as pool:
            fa, fb = pool.submit(a_offer), pool.submit(b_open)
            (ta, ra), (tb, rb) = fa.result(), fb.result()
        log(f"  A offer took {ta:.1f} s -> {show(ra)}\n  B open took {tb:.1f} s -> {show(rb)}")
        both = ta + tb
        case("concurrent", rb.get("_state") == "TASK_STATE_INPUT_REQUIRED" and max(ta, tb) < both * 0.9,
             f"A {ta:.1f} s and B {tb:.1f} s overlapped (serial would be about {both:.1f} s)")
        for t in (a, rb):
            if t.get("id"):
                c.cancel(t["id"])
    finally:
        proc.terminate(); proc.wait(timeout=10)

    proc, c = start_seller("hold", {"SELLER_HOLD_IDLE": "5"})
    try:
        log("\n### S/hold_lapse: A opens the lamp and goes quiet; B asks right away, then after 6 s")
        a = c.text("I want to buy a lamp")
        b1 = c.text("I want to buy a lamp")
        log(f"  B at once  <- {show(b1)}")
        time.sleep(6)
        b2 = c.text("I want to buy a lamp")
        log(f"  B after 6 s <- {show(b2)}")
        a2 = c.text("110", a["id"])
        log(f"  A comes back <- {show(a2)}")
        case("hold_lapse", b1["_state"] == "TASK_STATE_REJECTED" and b2["_state"] == "TASK_STATE_INPUT_REQUIRED"
             and a2.get("_state") == "TASK_STATE_REJECTED", "held, then lapsed, quiet buyer told to start again")
        c.cancel(b2["id"])
    finally:
        proc.terminate(); proc.wait(timeout=10)

    proc, c = start_seller("no-harness", {"HARNESS_CMD": "/nonexistent/claude -p"})
    try:
        log("\n### S/no_harness: the harness command does not exist")
        t = c.text("I want to buy a lamp")
        log(f"  <- {show(t)}")
        asks = [t["_ask"]]
        for o in ("80", "95", "120"):
            if t.get("_state") != "TASK_STATE_INPUT_REQUIRED":
                break
            t = c.text(o, t["id"])
            log(f"  -> {o!r}\n  <- {show(t)}")
            asks.append(t.get("_ask"))
        price = next((p["data"]["price"] for art in t.get("artifacts", []) for p in art.get("parts", [])
                      if isinstance(p.get("data"), dict)), None)
        case("no_harness", all(x is None or x >= 100 for x in asks) and (price is None or price >= 100) and "error" not in t,
             f"asks {asks}, final {t.get('_state')}, price {price}")
    finally:
        proc.terminate(); proc.wait(timeout=10)
    return results


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = part_b() + part_s()
    summary = {"passed": all(r["passed"] for r in results), "results": results}
    (OUT / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    (OUT / "driver.log").write_text("\n".join(LINES) + "\n")
    print(f"\nrobustness: {sum(r['passed'] for r in results)}/{len(results)} passed")


if __name__ == "__main__":
    main()
