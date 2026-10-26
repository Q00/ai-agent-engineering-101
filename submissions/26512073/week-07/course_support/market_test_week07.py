#!/usr/bin/env python3
"""Week-07 market test. Runs your shop and your market buyer against fixed counterparts over A2A v1.0.

Usage: python scripts/market_test_week07.py submissions/<student-id>/week-07

Reads agent.json in that directory:
    {
      "harness": "what drives your agents, e.g. claude -p --model haiku",
      "seller": {"cmd": "python seller.py --port {port}"},
      "buyer":  {"cmd": "python buyer.py --url {url} --item {item} --budget {budget}"},
      "market_buyer": {"cmd": "python market_buyer.py --shops {shops} --item {item} --budget {budget}"}
    }
Commands run with the submission directory as the working directory.

Seller tests: the week-06 seller tests, and every receipt must be {shop, item, price, task} with
              task = the A2A task id, a whole-number price, and sig_seller that verifies with
              shop_key.pem (created here if missing, the same file market_kit.py uses).
Buyer tests:  your market_buyer gets a shop list ({shops}) of rule-based shops that this script
              serves. Its last output line must be
                  {"shop": "<card URL>", "receipt": {...} or null, "stars": 1-5, "review": "..."}
              buyer_reads_reviews lists two shops that behave the same; one has bad reviews.
Every shop sells lamp, chair, bookshelf with reserves 100, 180, 260.

Writes test-report.json and logs/market-test.log into the submission directory.
Needs: pip install "a2a-sdk[http-server]" uvicorn httpx cryptography
"""
import base64
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
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KIT_VERSION = "1"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
REPEATS = 3
MAX_MESSAGES = 8          # messages the kit buyer sends in one negotiation
BUYER_TIMEOUT = 300       # seconds for one run of your buyer
DEAL = {"item": "lamp", "budget": 120, "ladder": [60, 80, 100, 110, 120]}
NO_DEAL = {"item": "bookshelf", "budget": 230, "ladder": [140, 170, 200, 230]}
RECEIPT_KEYS = ("shop", "item", "price", "task")
BAD_REVIEWS = [{"stars": 1, "review": "Agreed 110, the receipt said 135.", "round": 1},
               {"stars": 1, "review": "Receipt price was higher than the price we agreed.", "round": 1},
               {"stars": 2, "review": "Overcharged on the receipt, avoid.", "round": 1}]
GOOD_REVIEWS = [{"stars": 5, "review": "Fair price, receipt matched.", "round": 1},
                {"stars": 4, "review": "Came down to my budget, honest receipt.", "round": 1},
                {"stars": 5, "review": "Quick deal at a fair price.", "round": 1}]

LOG = []


def log(line=""):
    print(line)
    LOG.append(line)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def whole(x):
    """A2A data Parts carry numbers as doubles: 120 arrives as 120.0."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def shop_key(sub):
    p = sub / "shop_key.pem"
    if not p.exists():
        k = Ed25519PrivateKey.generate()
        p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        log("created shop_key.pem (do not commit it)")
    gi = sub / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    if "*.pem" not in lines:
        gi.write_text("\n".join(lines + ["*.pem"]) + "\n", encoding="utf-8")
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def sig_ok(pub, receipt):
    try:
        body = {k: whole(receipt.get(k)) for k in RECEIPT_KEYS}
        pub.verify(base64.b64decode(receipt["sig_seller"]), canon(body))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False


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
        else:                                       # shell=True: kill cmd.exe and the python under it
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def fill(cmd, **kw):
    for k, v in kw.items():
        cmd = cmd.replace("{" + k + "}", shlex.quote(str(v)) if os.name != "nt" else f'"{v}"')
    return cmd


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


def receipt_of(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
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


# ---------------------------------------------------------------- kit shop (rule-based, recording, signing)

def kit_seller_app(port, record, name):
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

    key = Ed25519PrivateKey.generate()

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
                rec = {"shop": name, "item": deal["item"], "price": offer, "task": task.id}
                rec["sig_seller"] = base64.b64encode(key.sign(canon(rec))).decode()
                await up.add_artifact(parts=[new_data_part(rec)], name="receipt")
                await up.complete(say(f"Deal: {deal['item']} at {offer}."))
                record["deals"].append(rec)
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
            elif request.url.path.endswith("agent-card.json"):
                record["card_reads"] += 1
            return await call_next(request)

    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=name, description="Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
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


def serve_kit_seller(port, record, name):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(kit_seller_app(port, record, name), host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def new_record():
    return {"messages": 0, "continued": 0, "offers": [], "deals": [], "canceled": 0, "posts": 0, "bad_version": 0,
            "card_reads": 0}


# ---------------------------------------------------------------- tests

RESULTS = []


def result(test, run, ok, detail):
    RESULTS.append({"test": test, "run": run, "pass": bool(ok), "detail": detail})
    log(f"  {'PASS' if ok else 'FAIL'}  {test} #{run}: {detail}")


def check_receipt(t, pub):
    """What is wrong with the receipt in a completed task, or None."""
    rec = receipt_of(t)
    if rec is None:
        return "no receipt data Part with a price"
    missing = [k for k in RECEIPT_KEYS if k not in rec]
    if missing:
        return f"receipt lacks {', '.join(missing)}"
    if rec["task"] != t.get("id"):
        return f"receipt task {rec['task']!r} is not the A2A task id {t.get('id')!r}"
    if not float(rec["price"]).is_integer():
        return f"price {rec['price']} is not a whole number"
    if "sig_seller" not in rec:
        return "receipt has no sig_seller"
    if not sig_ok(pub, rec):
        return "sig_seller does not verify with shop_key.pem over canon({shop, item, price, task})"
    return None


def seller_tests(sub, cfg):
    pub = shop_key(sub).public_key()
    port = free_port()
    base, url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port}/"
    out = open(sub / "logs" / "seller-under-test.log", "w", encoding="utf-8")
    cmd = fill(cfg["seller"]["cmd"], port=port)
    proc = start(cmd, sub, out)
    try:
        log(f"\n## seller tests: {cmd}")
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
            rec = receipt_of(t)
            price = rec.get("price") if rec else None
            low = [a for a in r.get("asks", []) if a < RESERVE["lamp"]]
            ok = (state(t) == "TASK_STATE_COMPLETED" and price is not None
                  and RESERVE["lamp"] <= price <= DEAL["budget"] and not low)
            result("seller_deal", run, ok, f"{state(t) or 'error'}, receipt price {price}, asks {r.get('asks')}")
            if state(t) == "TASK_STATE_COMPLETED":
                problem = check_receipt(t, pub)
                result("seller_signed_receipt", run, problem is None, problem or "sig_seller verifies with shop_key.pem")
            else:
                result("seller_signed_receipt", run, False, "no completed task to check")
            if run == 1 and r.get("task_id"):
                log("\n### seller_terminal: one more message to the finished task")
                t2, err = send(rpc_url, "110", r["task_id"])
                result("seller_terminal", 1, err is not None, "error returned" if err else f"no error, state {state(t2)}")
        for run in range(1, REPEATS + 1):
            log(f"\n### seller_holds_reserve #{run}: bookshelf, kit budget {NO_DEAL['budget']}, reserve {RESERVE['bookshelf']}")
            r = negotiate(rpc_url, NO_DEAL)
            t = r["task"]
            price = (receipt_of(t) or {}).get("price")
            sold_below = state(t) == "TASK_STATE_COMPLETED" and (price is None or price < RESERVE["bookshelf"])
            low = [a for a in r.get("asks", []) if a < RESERVE["bookshelf"]]
            result("seller_holds_reserve", run, r["opened"] and not sold_below and not low,
                   f"{state(t) or 'error'}, asks {r.get('asks')}"
                   + (f", sold at {price}" if state(t) == "TASK_STATE_COMPLETED" else ""))
        return card
    finally:
        stop(proc)
        out.close()


def last_json(text):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def run_market_buyer(sub, cfg, name, run, shops, sc):
    """Serve the listed kit shops, write {shops}, run the market buyer, return (output line, records, urls)."""
    servers, records, listing = [], [], []
    for s in shops:
        port = free_port()
        rec = new_record()
        servers.append(serve_kit_seller(port, rec, s["name"]))
        records.append(rec)
        listing.append({"name": s["name"], "url": f"http://127.0.0.1:{port}", "reviews": s["reviews"]})
    for s in listing:
        wait_card(s["url"], 15)
    shops_file = sub / ".market" / f"test-shops-{name}-{run}.json"
    shops_file.parent.mkdir(exist_ok=True)
    shops_file.write_text(json.dumps(listing, indent=1), encoding="utf-8")
    cmd = fill(cfg["market_buyer"]["cmd"], shops=shops_file, item=sc["item"], budget=sc["budget"])
    log(f"\n### {name} #{run}: {cmd}")
    logf = sub / "logs" / f"buyer-under-test-{name}-{run}.log"
    with open(logf, "w", encoding="utf-8") as out:
        proc = start(cmd, sub, out)
        try:
            proc.wait(timeout=BUYER_TIMEOUT)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        stop(proc)
    for srv in servers:
        srv.should_exit = True
    time.sleep(1.5)
    line = last_json(logf.read_text(encoding="utf-8", errors="ignore"))
    for s, rec in zip(listing, records):
        log(f"    {s['name']} saw: offers {rec['offers']}, deals {[d['price'] for d in rec['deals']]}, "
            f"canceled {rec['canceled']}, continued {rec['continued']}, posts without A2A-Version 1.0: {rec['bad_version']}")
    log(f"    last line: {json.dumps(line, ensure_ascii=False)[:300]}")
    return line, records, [s["url"] for s in listing], timed_out


def line_problem(line, urls):
    if not isinstance(line, dict):
        return "no JSON result line at the end of the output"
    if str(line.get("shop", "")).rstrip("/") not in urls:
        return f"shop {line.get('shop')!r} is not a card URL from {{shops}}"
    if "receipt" not in line or not (line["receipt"] is None or isinstance(line["receipt"], dict)):
        return "receipt must be an object or null"
    if not (isinstance(line.get("stars"), int) and 1 <= line["stars"] <= 5):
        return f"stars {line.get('stars')!r} is not a whole number from 1 to 5"
    if not str(line.get("review", "")).strip():
        return "review is empty"
    return None


def buyer_tests(sub, cfg):
    log("\n## buyer tests: your market_buyer against the kit's rule-based shops")
    for name, sc in (("buyer_deal", DEAL), ("buyer_holds_budget", NO_DEAL)):
        for run in range(1, REPEATS + 1):
            line, (rec,), urls, timed_out = run_market_buyer(sub, cfg, name, run, [{"name": "Kit Shop", "reviews": []}], sc)
            problem = line_problem(line, urls)
            over = [o for o in rec["offers"] if o > sc["budget"]]
            if name == "buyer_deal":
                issued = rec["deals"][0] if len(rec["deals"]) == 1 else None
                got = (line or {}).get("receipt") or {}
                if problem is None and issued is None:
                    problem = f"deals {[d['price'] for d in rec['deals']]}, expected one"
                if problem is None and (whole(got.get("price")) != issued["price"] or got.get("task") != issued["task"]
                                        or got.get("sig_seller") != issued["sig_seller"]):
                    problem = "the receipt in the result line is not the one the shop issued"
            elif problem is None and (rec["deals"] or (line or {}).get("receipt") is not None):
                problem = f"bought below reserve? deals {rec['deals']}, receipt {(line or {}).get('receipt')}"
            if problem is None and over:
                problem = f"offers above budget {sc['budget']}: {over}"
            if timed_out:
                problem = (problem + ", " if problem else "") + f"buyer still running after {BUYER_TIMEOUT} s"
            result(name, run, problem is None, problem or f"offers {rec['offers']}, stars {line['stars']}, "
                                                          f"receipt {'yes' if line['receipt'] else 'null'}")
            continued_ok = rec["continued"] > 0 or name == "buyer_holds_budget"
            result(name.replace("buyer_", "buyer_protocol_"), run,
                   rec["posts"] > 0 and rec["bad_version"] == 0 and continued_ok,
                   f"posts {rec['posts']}, without A2A-Version 1.0 {rec['bad_version']}, follow-ups with taskId {rec['continued']}")
    picks = []
    for run in range(1, REPEATS + 1):
        line, (bad, good), urls, _ = run_market_buyer(
            sub, cfg, "buyer_reads_reviews", run,
            [{"name": "Kit Shop North", "reviews": BAD_REVIEWS}, {"name": "Kit Shop South", "reviews": GOOD_REVIEWS}], DEAL)
        went = "good" if good["posts"] and not bad["posts"] else "bad" if bad["posts"] and not good["posts"] else \
               "both" if bad["posts"] else "none"
        picks.append(went)
    result("buyer_reads_reviews", 1, picks.count("good") >= 2,
           f"shop with good reviews in {picks.count('good')} of {REPEATS} runs (picks {picks}); needs 2")


def main():
    os.environ.setdefault("PYTHONUTF8", "1")                    # Windows: the agents under test read and print UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sub = Path(sys.argv[1]).resolve()
    cfg = json.loads((sub / "agent.json").read_text(encoding="utf-8"))
    (sub / "logs").mkdir(exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log(f"# week-07 market test, kit v{KIT_VERSION}, {started}")
    log(f"harness: {cfg.get('harness')}")
    if "market_buyer" not in cfg:
        sys.exit('agent.json has no "market_buyer" command')
    card = seller_tests(sub, cfg)
    buyer_tests(sub, cfg)
    passed = all(r["pass"] for r in RESULTS)
    kit = Path(__file__).read_bytes().replace(b"\r\n", b"\n")   # same hash after a CRLF checkout
    report = {"kit_version": KIT_VERSION, "kit_sha256": hashlib.sha256(kit).hexdigest(), "started": started,
              "harness": cfg.get("harness"), "seller_cmd": cfg["seller"]["cmd"],
              "market_buyer_cmd": cfg["market_buyer"]["cmd"], "seller_card": card, "results": RESULTS, "passed": passed}
    (sub / "test-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"\n{sum(r['pass'] for r in RESULTS)}/{len(RESULTS)} passed -> {'PASS' if passed else 'FAIL'}")
    (sub / "logs" / "market-test.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
