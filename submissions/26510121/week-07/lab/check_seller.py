"""Stage-3 checks using the current kit's fixed buyer, not a full trade report.

Starts seller.py directly (so Windows cleanup owns the actual server process).
Every run gets fresh logs. Never writes the assignment's test-report.json.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback
from contextlib import redirect_stdout

import httpx


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from negotiation.config import load_settings


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main():
    stamp = datetime.now(timezone.utc).strftime("stage-%Y%m%dT%H%M%S.%fZ")
    logs = ROOT / "logs" / "seller" / stamp
    logs.mkdir(parents=True, exist_ok=False)
    checks = []
    kit_path = ROOT.parents[2] / "scripts" / "trade_test_week06.py"
    kit = runpy.run_path(str(kit_path))
    settings = load_settings()

    def check(name, passed, detail):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)

    process = None
    error = None
    with (logs / "client-console.log").open("x", encoding="utf-8") as console, \
         (logs / "server-console.log").open("x", encoding="utf-8") as server_output:
        with redirect_stdout(Tee(sys.stdout, console)):
            try:
                if not settings.api_key:
                    raise RuntimeError("API key missing; live execution skipped")
                port = kit["free_port"]()
                base = f"http://127.0.0.1:{port}"
                command = [sys.executable, str(ROOT / "seller.py"), "--port", str(port),
                           "--log-dir", str(logs / "model")]
                print("$ " + subprocess.list2cmdline(command))
                env = os.environ.copy()
                env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", NO_PROXY="127.0.0.1,localhost")
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=server_output,
                                           stderr=subprocess.STDOUT,
                                           creationflags=0x08000000 if os.name == "nt" else 0)
                card = kit["wait_card"](base)
                if not card:
                    raise RuntimeError("No Agent Card within the kit's readiness deadline")
                interface = card.get("supportedInterfaces", [{}])[0]
                check("seller_card", card.get("name") and card.get("skills")
                      and interface.get("protocolBinding") == "JSONRPC"
                      and interface.get("protocolVersion") == "1.0" and interface.get("url"),
                      {"name": card.get("name"), "interface": interface})
                url = interface["url"]
                for run in range(1, 4):
                    print(f"\n## seller_deal #{run}: current kit buyer, lamp budget 120")
                    result = kit["negotiate"](url, kit["DEAL"])
                    task = result["task"]
                    price = kit["receipt_price"](task)
                    if run == 1:
                        check("seller_opens", result["opened"] and result.get("opened_ask") is not None,
                              {"state": result.get("opened_state"), "ask": result.get("opened_ask")})
                    check(f"seller_deal_{run}", kit["state"](task) == "TASK_STATE_COMPLETED"
                          and price is not None and 100 <= price <= 120
                          and not any(p < 100 for p in result.get("asks", [])),
                          {"state": kit["state"](task), "price": price, "asks": result.get("asks")})
                    if run == 1:
                        _, rpc_error = kit["send"](url, "110", result.get("task_id"))
                        check("seller_terminal", rpc_error is not None, rpc_error)
                for run in range(1, 4):
                    print(f"\n## seller_holds_reserve #{run}: current kit buyer, bookshelf budget 230")
                    result = kit["negotiate"](url, kit["NO_DEAL"])
                    task = result["task"]
                    price = kit["receipt_price"](task)
                    sold_below = kit["state"](task) == "TASK_STATE_COMPLETED" and (price is None or price < 260)
                    check(f"seller_holds_reserve_{run}", result["opened"] and not sold_below
                          and not any(p < 260 for p in result.get("asks", [])),
                          {"state": kit["state"](task), "price": price, "asks": result.get("asks")})
                print("\n## additional protocol checks")
                task, _ = kit["send"](url, "I want to buy a sofa")
                check("unknown_item_rejected", kit["state"](task) == "TASK_STATE_REJECTED", kit["state"](task))
                task, _ = kit["send"](url, "I want to buy a chair")
                canceled, _ = kit["rpc"](url, "CancelTask", {"id": task["id"]})
                check("seller_cancel", kit["state"](canceled) == "TASK_STATE_CANCELED", kit["state"](canceled))
                missing = httpx.post(url, json={"jsonrpc": "2.0", "id": "version-check", "method": "SendMessage",
                    "params": {"message": {"role": "ROLE_USER", "messageId": "version-check",
                                           "parts": [{"text": "I want to buy a lamp"}]}}}, timeout=5).json()
                check("missing_version_rejected", missing.get("error", {}).get("code") == -32009, missing)
            except Exception:
                error = traceback.format_exc()
                print(error)
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    passed = error is None and len(checks) == 12 and all(c["passed"] for c in checks)
    summary = {"kind": "seller-stage-checks-not-full-trade-test", "model": settings.model,
               "kit_sha256": hashlib.sha256(kit_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
               "passed": passed, "checks": checks, "error": error}
    with (logs / "seller-summary.json").open("x", encoding="utf-8") as output:
        json.dump(summary, output, ensure_ascii=False, indent=2)
    print(f"Seller stage: {sum(c['passed'] for c in checks)}/{len(checks)} passed; logs: {logs}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
