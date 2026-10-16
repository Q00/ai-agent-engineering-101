"""Live STEP 4 exercise: counteroffer, deal, rejection and cancel with one harness."""

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["claude", "codex"], required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logs = root / "logs/step4" / (stamp + "-" + args.mode)
    logs.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    report = {"mode": args.mode, "url": url, "scope": "seller state transitions, not official trade test",
              "passed": False, "results": []}
    counter = 0
    with (logs / "server.stdout.txt").open("wb") as stdout, (logs / "server.stderr.txt").open("wb") as stderr:
        server = subprocess.Popen([sys.executable, str(root / "seller.py"), "--mode", args.mode,
                                   "--port", str(port)], cwd=root, stdout=stdout, stderr=stderr)
        try:
            with httpx.Client(headers={"A2A-Version": "1.0"}, timeout=110, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Seller exited during startup")
                    try:
                        card = client.get(url + "/.well-known/agent-card.json", timeout=1)
                        card.raise_for_status()
                        (logs / "card.json").write_bytes(card.content)
                        break
                    except httpx.HTTPError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Startup timed out")
                        time.sleep(0.1)

                def rpc(method, params):
                    nonlocal counter
                    counter += 1
                    request = {"jsonrpc": "2.0", "id": str(uuid4()), "method": method, "params": params}
                    (logs / f"{counter:02d}.request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
                    response = client.post(url + "/", json=request)
                    (logs / f"{counter:02d}.response.json").write_bytes(response.content)
                    response.raise_for_status()
                    payload = response.json()
                    assert "error" not in payload, payload
                    task = payload["result"].get("task", payload["result"])
                    summary = {"request": counter, "method": method, "task_id": task["id"],
                               "state": task["status"]["state"]}
                    report["results"].append(summary)
                    print(json.dumps(summary), flush=True)
                    return task

                def send(text, tid=None):
                    message = {"role": "ROLE_USER", "messageId": str(uuid4()), "parts": [{"text": text}]}
                    if tid:
                        message["taskId"] = tid
                    return rpc("SendMessage", {"message": message})

                def state(task):
                    return task["status"]["state"]

                def asking(task, reserve):
                    assert state(task) == "TASK_STATE_INPUT_REQUIRED", task
                    text = " ".join(p.get("text", "") for p in task["status"]["message"]["parts"])
                    nums = re.findall(r"\d+", text)
                    assert nums and int(nums[-1]) >= reserve, task
                    return int(nums[-1])

                lamp = send("I want to buy a lamp")
                asking(lamp, 100)
                tid = lamp["id"]
                counteroffer = send("80", tid)
                price = asking(counteroffer, 100)
                assert counteroffer["id"] == tid
                completed = send(str(price), tid)
                assert state(completed) == "TASK_STATE_COMPLETED", completed
                assert completed["id"] == tid
                receipts = [part["data"] for artifact in completed.get("artifacts", [])
                            for part in artifact.get("parts", []) if "data" in part]
                assert len(receipts) == 1 and receipts[0]["price"] == price, completed
                assert receipts[0]["item"] == "lamp", completed
                report["receipt"] = receipts[0]

                bookshelf = send("I want to buy a bookshelf")
                asking(bookshelf, 260)
                book_tid = bookshelf["id"]
                for index in range(3):
                    bookshelf = send("1", book_tid)
                    assert bookshelf["id"] == book_tid
                    if index < 2:
                        asking(bookshelf, 260)
                assert state(bookshelf) == "TASK_STATE_REJECTED", bookshelf
                assert not bookshelf.get("artifacts"), bookshelf

                chair = send("I want to buy a chair")
                asking(chair, 180)
                canceled = rpc("CancelTask", {"id": chair["id"]})
                assert state(canceled) == "TASK_STATE_CANCELED", canceled
                assert not canceled.get("artifacts"), canceled
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
            print(f"STEP 4 logs: {logs}", flush=True)


if __name__ == "__main__":
    main()
