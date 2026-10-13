#!/usr/bin/env python3
"""Week-06 trade test. Runs your buyer and your seller against fixed counterparts over A2A v1.0.

Usage: python scripts/trade_test_week06.py submissions/<student-id>/week-06

Reads agent.json in that directory:
    {
      "harness": "what drives your agents, e.g. claude -p --model haiku",
      "seller": {"cmd": "python seller.py --port {port}"},
      "buyer":  {"cmd": "python buyer.py --url {url} --item {item} --budget {budget}"}
    }
Commands run with the submission directory as the working directory.

Seller tests: this script plays a buyer with a fixed offer ladder against your seller.
              The asking price is the last number in each input-required message;
              an ask below the reserve fails the test even if no deal follows.
Buyer tests:  your buyer trades with a rule-based seller that this script serves.
Every catalog seller sells lamp, chair, bookshelf with reserves 100, 180, 260.

Writes test-report.json and logs/trade-test.log into the submission directory.
Needs: pip install "a2a-sdk[http-server]" uvicorn httpx
"""
import hashlib
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

KIT_VERSION = "1"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
REPEATS = 3
MAX_MESSAGES = 8          # messages the kit buyer sends in one negotiation
BUYER_TIMEOUT = 300       # seconds for one run of your buyer
DEAL = {"item": "lamp", "budget": 120, "ladder": [60, 80, 100, 110, 120]}
NO_DEAL = {"item": "bookshelf", "budget": 230, "ladder": [140, 170, 200, 230]}

LOG = []


def log(line=""):
    print(line)
    LOG.append(line)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(cmd, cwd, out):
    kw = {"start_new_session": True} if os.name != "nt" else {}
    return subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, **kw)


def stop(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def wait_card(base, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            r = httpx.get(base + "/.well-known/agent-card.json", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1)
    return None


# ---------------------------------------------------------------- A2A client (kit buyer)

def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
    if "error" in r:
        log(f"    <- error {r['error'].get('code')} {r['error'].get('message', '')[:120]}")
        return None, r["error"]
    t = r["result"].get("task", r["result"])
    if "status" not in t:                              # a plain Message, not a task
        log(f"    <- message (no task) {json.dumps(t)[:120]}")
        return t, None
    text = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
    log(f"    <- {t['status']['state']} | {text[:160]}")
    for a in t.get("artifacts", []):
        log(f"       artifact {a.get('name')} {json.dumps([p.get('data', p.get('text')) for p in a.get('parts', [])])}")
    return t, None


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"    -> SendMessage {json.dumps(text)}" + (f" taskId={task_id[:8]}" if task_id else ""))
    return rpc(url, "SendMessage", {"message": msg})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def last_number(t):
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


def negotiate(url, sc):
    """Fixed buyer: walk up the ladder; accept the seller's ask as soon as it is within budget."""
    t, err = send(url, f"I want to buy a {sc['item']}")
    if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
        return {"opened": False, "task": t}
    tid, opened_ask, opened_state = t["id"], last_number(t), state(t)
    ladder, asks = list(sc["ladder"]), []
    for _ in range(MAX_MESSAGES):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask = last_number(t)
        if ask is not None:
            asks.append(ask)
        if ask is not None and ask <= sc["budget"]:
            offer = ask
        else:
            offer = ladder.pop(0) if ladder else sc["budget"]
        t, err = send(url, str(offer), tid)
        if t is None:
            break
    if state(t) == "TASK_STATE_INPUT_REQUIRED" and last_number(t) is not None:
        asks.append(last_number(t))
    return {"opened": True, "opened_ask": opened_ask, "opened_state": opened_state, "task": t, "task_id": tid,
            "asks": asks}


# ---------------------------------------------------------------- kit seller (rule-based, recording)

def kit_seller_app(port, record):
    import uvicorn  # noqa: F401  (imported here so the seller tests run without it)
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

    class Seller(AgentExecutor):
        def __init__(self):
            self.open = {}

        async def execute(self, context, event_queue):
            task = context.current_task
            record["messages"] += 1
            if task is None:
                task = new_task_from_user_message(context.message)
                await event_queue.enqueue_event(task)
            else:
                record["continued"] += 1
            up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
            text = get_message_text(context.message) or ""
            deal = self.open.get(task.id)
            if deal is None:
                item = next((k for k in RESERVE if k in text.lower()), None)
                if item is None:
                    await up.reject(say("We only sell: lamp, chair, bookshelf."))
                    return
                ask = math.ceil(RESERVE[item] * 1.3)
                self.open[task.id] = {"item": item, "ask": ask, "rounds": 0}
                await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
                return
            m = re.search(r"\d+", text)
            if m is None:
                await up.requires_input(say("Send your offer as a number."))
                return
            offer, reserve = int(m.group()), RESERVE[deal["item"]]
            record["offers"].append(offer)
            if offer >= reserve:
                await up.add_artifact(parts=[new_data_part({"seller": "Kit Shop", "item": deal["item"], "price": offer})],
                                      name="receipt")
                await up.complete(say(f"Deal: {deal['item']} at {offer}."))
                record["deals"].append(offer)
                del self.open[task.id]
                return
            deal["rounds"] += 1
            if deal["rounds"] >= 5:
                await up.reject(say(f"No deal at {offer}."))
                del self.open[task.id]
                return
            deal["ask"] = max(reserve, (offer + deal["ask"]) // 2)
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))

        async def cancel(self, context, event_queue):
            task = context.current_task
            self.open.pop(task.id, None)
            record["canceled"] += 1
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()

    class Recorder(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.method == "POST":
                record["posts"] += 1
                if request.headers.get("A2A-Version") != "1.0":
                    record["bad_version"] += 1
            return await call_next(request)

    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name="Kit Shop", description="Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
        version="1.0.0", default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt.",
                           tags=["shopping"], examples=["I want to buy a lamp", "90"])])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))
    app.add_middleware(Recorder)
    return app


def serve_kit_seller(port, record):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(kit_seller_app(port, record), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    return server


# ---------------------------------------------------------------- tests

RESULTS = []


def result(test, run, ok, detail):
    RESULTS.append({"test": test, "run": run, "pass": bool(ok), "detail": detail})
    log(f"  {'PASS' if ok else 'FAIL'}  {test} #{run}: {detail}")


def seller_tests(sub, cfg):
    port = free_port()
    base, url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port}/"
    out = open(sub / "logs" / "seller-under-test.log", "w")
    proc = start(cfg["seller"]["cmd"].format(port=port), sub, out)
    try:
        log(f"\n## seller tests: {cfg['seller']['cmd'].format(port=port)}")
        card = wait_card(base)
        if card is None:
            result("seller_card", 1, False, "no Agent Card at /.well-known/agent-card.json within 60 s")
            return None
        iface = (card.get("supportedInterfaces") or [{}])[0]
        ok = (card.get("name") and card.get("skills") and iface.get("protocolBinding") == "JSONRPC"
              and str(iface.get("protocolVersion")) == "1.0" and iface.get("url"))
        result("seller_card", 1, ok, f"name={card.get('name')!r} binding={iface.get('protocolBinding')} "
                                     f"version={iface.get('protocolVersion')} skills={len(card.get('skills') or [])}")
        rpc_url = iface.get("url") or url
        for run in range(1, REPEATS + 1):
            log(f"\n### seller_deal #{run}: lamp, kit budget {DEAL['budget']}, reserve {RESERVE['lamp']}")
            r = negotiate(rpc_url, DEAL)
            if run == 1:
                result("seller_opens", 1, r["opened"] and r.get("opened_ask") is not None,
                       f"first message -> {r.get('opened_state') or state(r['task']) or 'error'}, ask {r.get('opened_ask')}")
            t = r["task"]
            price = receipt_price(t)
            low = [a for a in r.get("asks", []) if a < RESERVE["lamp"]]
            ok = (state(t) == "TASK_STATE_COMPLETED" and price is not None
                  and RESERVE["lamp"] <= price <= DEAL["budget"] and not low)
            result("seller_deal", run, ok, f"{state(t) or 'error'}, receipt price {price}, asks {r.get('asks')}")
            if run == 1 and r.get("task_id"):
                log("\n### seller_terminal: one more message to the finished task")
                t2, err = send(rpc_url, "110", r["task_id"])
                result("seller_terminal", 1, err is not None, "error returned" if err else f"no error, state {state(t2)}")
        for run in range(1, REPEATS + 1):
            log(f"\n### seller_holds_reserve #{run}: bookshelf, kit budget {NO_DEAL['budget']}, reserve {RESERVE['bookshelf']}")
            r = negotiate(rpc_url, NO_DEAL)
            t = r["task"]
            price = receipt_price(t)
            sold_below = state(t) == "TASK_STATE_COMPLETED" and (price is None or price < RESERVE["bookshelf"])
            low = [a for a in r.get("asks", []) if a < RESERVE["bookshelf"]]
            result("seller_holds_reserve", run, r["opened"] and not sold_below and not low,
                   f"{state(t) or 'error'}, asks {r.get('asks')}"
                   + (f", sold at {price}" if state(t) == "TASK_STATE_COMPLETED" else ""))
        return card
    finally:
        stop(proc)
        out.close()


def buyer_tests(sub, cfg):
    log("\n## buyer tests: your buyer against the kit's rule-based seller")
    for name, sc in (("buyer_deal", DEAL), ("buyer_holds_budget", NO_DEAL)):
        for run in range(1, REPEATS + 1):
            port = free_port()
            url = f"http://127.0.0.1:{port}"
            record = {"messages": 0, "continued": 0, "offers": [], "deals": [], "canceled": 0, "posts": 0, "bad_version": 0}
            server = serve_kit_seller(port, record)
            wait_card(url, 15)
            cmd = cfg["buyer"]["cmd"].format(url=url, item=sc["item"], budget=sc["budget"])
            log(f"\n### {name} #{run}: {cmd}")
            out = open(sub / "logs" / f"buyer-under-test-{name}-{run}.log", "w")
            proc = start(cmd, sub, out)
            try:
                proc.wait(timeout=BUYER_TIMEOUT)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
            stop(proc)
            out.close()
            server.should_exit = True
            time.sleep(1.5)
            log(f"    kit seller saw: offers {record['offers']}, deals {record['deals']}, canceled {record['canceled']}, "
                f"continued {record['continued']}, posts without A2A-Version 1.0: {record['bad_version']}")
            if name == "buyer_deal":
                ok = len(record["deals"]) == 1 and record["deals"][0] <= sc["budget"]
                detail = f"deals {record['deals']} (budget {sc['budget']})"
            else:
                ok = not record["deals"]
                detail = f"deals {record['deals']} (budget {sc['budget']} < reserve {RESERVE[sc['item']]})"
            if timed_out:
                ok, detail = False, detail + f", buyer still running after {BUYER_TIMEOUT} s"
            result(name, run, ok, detail)
            continued_ok = record["continued"] > 0 or name == "buyer_holds_budget"   # walking away at once is fine
            result(name.replace("buyer_", "buyer_protocol_"), run,
                   record["posts"] > 0 and record["bad_version"] == 0 and continued_ok,
                   f"posts {record['posts']}, without A2A-Version 1.0 {record['bad_version']}, follow-ups with taskId {record['continued']}")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sub = Path(sys.argv[1]).resolve()
    cfg = json.loads((sub / "agent.json").read_text(encoding="utf-8"))
    (sub / "logs").mkdir(exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log(f"# week-06 trade test, kit v{KIT_VERSION}, {started}")
    log(f"harness: {cfg.get('harness')}")
    card = seller_tests(sub, cfg)
    buyer_tests(sub, cfg)
    passed = all(r["pass"] for r in RESULTS)
    kit = Path(__file__).read_bytes().replace(b"\r\n", b"\n")   # same hash after a CRLF checkout
    report = {"kit_version": KIT_VERSION, "kit_sha256": hashlib.sha256(kit).hexdigest(), "started": started,
              "harness": cfg.get("harness"), "seller_cmd": cfg["seller"]["cmd"], "buyer_cmd": cfg["buyer"]["cmd"],
              "seller_card": card, "results": RESULTS, "passed": passed}
    (sub / "test-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"\n{sum(r['pass'] for r in RESULTS)}/{len(RESULTS)} passed -> {'PASS' if passed else 'FAIL'}")
    (sub / "logs" / "trade-test.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
