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

import httpx


AGENT = Path(__file__).resolve().parent.parent
REPO = AGENT.parents[2]
spec = importlib.util.spec_from_file_location("course_market_kit", REPO / "scripts" / "market_kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


original_call = kit.Market.call
original_handle_order = kit.handle_order
original_join = kit.cmd_join


def join(args):
    if args.shop_only and args.tunnel:
        from shop_runtime import run_shop
        return run_shop(args, kit)
    return original_join(args)


def market_call(self, method, path, role="buyer", **kwargs):
    response = original_call(self, method, path, role=role, **kwargs)
    if response.status_code != 200:
        return response
    if method == "GET" and path == "/api/shops":
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
        memory = AGENT / "memory" / "market-verification.jsonl"
        memory.parent.mkdir(exist_ok=True)
        with memory.open("a", encoding="utf-8") as out:
            out.write(json.dumps(event) + "\n")
    return response


def handle_order(order, *args, **kwargs):
    names = ("MARKET_ROUND", "MARKET_REGISTRY_KEYS")
    old = {name: os.environ.get(name) for name in names}
    os.environ["MARKET_ROUND"] = str(order["round"])
    os.environ["MARKET_REGISTRY_KEYS"] = str(AGENT / ".market" / "registry-keys.json")
    try:
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
    kit.open_tunnel = open_tunnel
    kit.Market.call = market_call
    kit.handle_order = handle_order
    kit.cmd_join = join
    kit.main()
