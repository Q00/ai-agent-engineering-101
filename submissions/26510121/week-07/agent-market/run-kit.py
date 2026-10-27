"""Run the unmodified course kit with visible, bounded tunnel diagnostics."""
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import sqlite3

import httpx


AGENT = Path(__file__).resolve().parent.parent
REPO = AGENT.parents[2]
sys.path.insert(0, str(AGENT))
from negotiation.auditing import take_audit_request
from negotiation.experience import Experience
spec = importlib.util.spec_from_file_location("course_market_kit", REPO / "scripts" / "market_kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


original_call = kit.Market.call
original_handle_order = kit.handle_order
original_join = kit.cmd_join
experience = None


def remember(method, *args, **kwargs):
    if experience is None:
        return
    try:
        return getattr(experience, method)(*args, **kwargs)
    except (OSError, sqlite3.Error, ValueError, TypeError) as failure:
        kit.log(f'[memory] {method} unavailable: {type(failure).__name__}; raw logs retained')


def join(args):
    if args.shop_only and args.tunnel:
        from shop_runtime import run_shop
        return run_shop(args, kit)
    return original_join(args)


def market_call(self, method, path, role="buyer", **kwargs):
    requested=time.monotonic()
    if method=='POST' and path=='/api/orders' and self is not None and not getattr(self,'_ranking_cycle_start',None):
        self._ranking_cycle_start=requested
    response = original_call(self, method, path, role=role, **kwargs)
    if method=='POST' and path=='/api/orders' and response.status_code==200 and self is not None:
        self._ranking_order_received=time.monotonic()
    if method=='POST' and path=='/api/results' and response.status_code in range(400,500) and response.status_code!=429:
        submitted=kwargs.get('json',{});receipt=submitted.get('receipt') or {}
        total=time.monotonic()-(getattr(self,'_ranking_cycle_start',None) or requested) if self is not None else None
        remember('buyer_feedback',task=receipt.get('task'),order=submitted.get('order'),verified=False,settled=False,
                 round_number=int(os.environ.get('MARKET_ROUND','0')),total_seconds=total,
                 failed_checks=[f'result_http_{response.status_code}'])
        if self is not None:
            self._ranking_cycle_start=None
            self._ranking_order_received=None
    if response.status_code != 200:
        return response
    if method == "GET" and path == "/api/shops":
        remember('set_round', response.json()['round'])
        registry = {row["url"].rstrip("/"): {"name": row["name"], "pubkey": row["pubkey"]}
                    for row in response.json()["shops"]}
        target = AGENT / ".market" / "registry-keys.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(registry), encoding="utf-8")
        temporary.replace(target)
    elif method == "POST" and path == "/api/results":
        submitted, result = kwargs.get("json", {}), response.json()
        receipt = submitted.get("receipt") or {}
        event = {"at": datetime.now().isoformat(), "order": submitted.get("order"),
                 "task_id": receipt.get("task"), "shop_url": submitted.get("shop"),
                 "verified": result.get("verified") is True, "checks": result.get("checks"),
                 "review_accepted": result.get("review_accepted"), "ledger": result.get("ledger")}
        round_number = int(os.environ.get('MARKET_ROUND', '0'))
        event['round'] = round_number
        event['settled'] = round_number >= 3 and bool(submitted.get('mandate_receipt')) and result.get('verified') is True
        complete=time.monotonic()
        began=getattr(self,'_ranking_cycle_start',None) if self is not None else None
        received=getattr(self,'_ranking_order_received',None) if self is not None else None
        event['total_seconds']=complete-began if began is not None else None
        event['processing_seconds']=complete-received if received is not None else None
        event['wait_seconds']=received-began if received is not None and began is not None else None
        event['failed_checks']=[k for k,v in (result.get('checks') or {}).items() if v is False]
        remember('buyer_feedback', task=receipt.get('task'), order=submitted.get('order'),
                 verified=result.get('verified'), settled=event['settled'], round_number=round_number,
                 ledger=result.get('ledger'),total_seconds=event['total_seconds'],
                 processing_seconds=event['processing_seconds'],wait_seconds=event['wait_seconds'],
                 failed_checks=event['failed_checks'])
        if self is not None:
            self._ranking_cycle_start=None
            self._ranking_order_received=None
        memory = AGENT / "memory" / "market-verification.jsonl"
        memory.parent.mkdir(exist_ok=True)
        with memory.open("a", encoding="utf-8") as out:
            out.write(json.dumps(event) + "\n")
    elif method == 'POST' and path == '/api/settle' and role == 'shop':
        submitted, result = kwargs.get('json', {}), response.json()
        if result.get('mandate_receipt'):
            key = kit.load_key(AGENT / 'shop_key.pem')
            checkout = kit.jwt_payload(submitted.get('checkout_jwt', ''), kit.pub_b64(key))
            if checkout:
                remember('set_round', 3)
                remember('seller_settlement', task=checkout['task'],item=checkout['item'],price=checkout['price'],
                         order=submitted.get('order'),ledger=result.get('ledger'))
    return response


def handle_order(order, *args, **kwargs):
    names = ("MARKET_ROUND", "MARKET_REGISTRY_KEYS", "MARKET_AUDIT_SHOP",'MARKET_ORDER_ID')
    old = {name: os.environ.get(name) for name in names}
    os.environ["MARKET_ROUND"] = str(order["round"])
    os.environ['MARKET_ORDER_ID']=str(order['order'])
    remember('set_round', order['round'])
    os.environ["MARKET_REGISTRY_KEYS"] = str(AGENT / ".market" / "registry-keys.json")
    try:
        audit = take_audit_request(AGENT / ".market" / "audit-request.json", order)
        if audit:
            os.environ["MARKET_AUDIT_SHOP"] = audit
            kit.log(f"[audit] one real order assigned to integrity audit of {audit}; normal budget and receipt checks remain")
        else:
            os.environ.pop("MARKET_AUDIT_SHOP", None)
        return original_handle_order(order, *args, **kwargs)
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def open_tunnel(port):
    # This is exactly the kit's dedicated proxy, never an arbitrary local service.
    card = httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=5)
    card.raise_for_status()
    if card.json().get("name") != "Seochanit Shop":
        raise RuntimeError("Refusing to expose an unexpected local service")
    path = AGENT / "logs" / "market-sessions" / datetime.now().strftime("tunnel-raw-%Y%m%d-%H%M%S-%f.log")
    provider = os.environ.get("MARKET_TUNNEL_PROVIDER", "cloudflare")
    if provider == "pinggy":
        command = [sys.executable, "-u", str(Path(__file__).with_name("pinggy-tunnel.py")), "--port", str(port)]
    elif provider == "cloudflare":
        command = ["cloudflared", "tunnel", "--no-autoupdate", "--edge-ip-version", "4",
                   "--protocol", "http2", "--url", f"http://127.0.0.1:{port}"]
    else:
        raise RuntimeError("Unsupported tunnel provider")
    proc = subprocess.Popen(command,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env={key: value for key, value in os.environ.items()
                                 if key not in {"OPENAI_API_KEY", "MARKET_OWNER"}})
    lines = queue.Queue()

    def capture():
        with path.open("x", encoding="utf-8") as out:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                lines.put(line)
        lines.put(None)

    threading.Thread(target=capture, daemon=True).start()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            continue
        if line is None:
            proc.wait()
            raise RuntimeError(f"{provider} exited {proc.returncode}; see {path.relative_to(AGENT)}")
        match = (re.search(r"TUNNEL_URL (https://[^\s]+)", line) if provider == "pinggy"
                 else re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line))
        if match:
            return proc, match.group(1)
    kit.stop(proc)
    raise RuntimeError(f"{provider} did not provision a tunnel; see {path.relative_to(AGENT)}")


if __name__ == "__main__":
    try:
        experience = Experience()
    except (OSError, sqlite3.Error) as failure:
        kit.log(f'[memory] opening journal failed: {type(failure).__name__}; raw logs retained')
    if os.name == "nt":
        import ctypes
        # Prevent automatic idle sleep only while this kit process is alive.
        # The OS releases this request when stop.ps1 terminates the process.
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
            raise RuntimeError("Could not keep the market host awake")
    kit.open_tunnel = open_tunnel
    kit.Market.call = market_call
    kit.handle_order = handle_order
    kit.cmd_join = join
    kit.main()
