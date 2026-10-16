"""Live STEP 3 check. Starts only its own seller; makes three paid harness calls."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

from seller import RESERVES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["claude", "codex"], required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logs = root / "logs/step3" / (stamp + "-" + args.mode)
    logs.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    report = {"mode": args.mode, "url": base_url, "scope": "opening quotes only", "passed": False,
              "results": []}
    with (logs / "server.stdout.txt").open("wb") as stdout, (logs / "server.stderr.txt").open("wb") as stderr:
        server = subprocess.Popen([sys.executable, str(root / "seller.py"), "--mode", args.mode,
                                   "--port", str(port)], cwd=root, stdout=stdout, stderr=stderr)
        try:
            with httpx.Client(headers={"A2A-Version": "1.0"}, timeout=110, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Seller exited; see server.stderr.txt")
                    try:
                        response = client.get(base_url + "/.well-known/agent-card.json", timeout=1)
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Seller startup timed out")
                        time.sleep(0.1)
                (logs / "card.json").write_bytes(response.content)
                card = response.json()
                interface = card["supportedInterfaces"][0]
                assert interface["protocolBinding"] == "JSONRPC", interface
                assert interface["protocolVersion"] == "1.0", interface
                assert interface["url"] == base_url + "/", interface
                assert card["name"] and card["skills"], card
                for item, reserve in RESERVES.items():
                    request = {"jsonrpc": "2.0", "id": str(uuid4()), "method": "SendMessage",
                               "params": {"message": {"role": "ROLE_USER", "messageId": str(uuid4()),
                                          "parts": [{"text": f"I want to buy a {item}"}]}}}
                    (logs / f"{item}.request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
                    response = client.post(interface["url"], json=request)
                    (logs / f"{item}.response.json").write_bytes(response.content)
                    response.raise_for_status()
                    payload = response.json()
                    assert "error" not in payload, payload
                    task = payload["result"].get("task", payload["result"])
                    state = task["status"]["state"]
                    text = " ".join(part.get("text", "") for part in task["status"]["message"]["parts"])
                    numbers = re.findall(r"\d+(?:\.\d+)?", text)
                    assert state == "TASK_STATE_INPUT_REQUIRED", task
                    assert numbers and float(numbers[-1]) >= reserve, task
                    assert task["id"] and task["contextId"], task
                    assert not task.get("artifacts"), task
                    result = {"item": item, "state": state, "ask": float(numbers[-1]), "task_id": task["id"]}
                    report["results"].append(result)
                    print(json.dumps(result), flush=True)
                assert len({entry["task_id"] for entry in report["results"]}) == len(RESERVES)
                report["passed"] = True
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
            (logs / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"STEP 3 logs: {logs}", flush=True)


if __name__ == "__main__":
    main()
