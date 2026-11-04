"""Exercise descriptive catalog search and save a genuine A2A trace."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from catalog import load

ROOT = Path(__file__).resolve().parents[1]
QUERY = "something wooden to keep about 40 books, not taller than my desk"


def port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def send(client, url, text, task_id=None):
    message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    response = client.post(url, json={"jsonrpc": "2.0", "id": str(uuid.uuid4()),
                                      "method": "SendMessage", "params": {"message": message}},
                           headers={"A2A-Version": "1.0"})
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"].get("task", body["result"])

def mask_id(task_id: str | None) -> str:
    if not task_id:
        return "<none>"
    return task_id[:4] + "..." + task_id[-4:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "challenge-1")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    address = port()
    with (args.output / "seller.log").open("w") as log:
        proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(address)],
                                cwd=ROOT, env=os.environ.copy(), stdout=log,
                                stderr=subprocess.STDOUT)
        try:
            with httpx.Client(trust_env=False, timeout=100) as client:
                url = f"http://127.0.0.1:{address}/"
                for _ in range(60):
                    try:
                        card = client.get(url + ".well-known/agent-card.json").json()
                        break
                    except (httpx.RequestError, ValueError):
                        if proc.poll() is not None:
                            raise RuntimeError("Seller exited; inspect seller.log")
                        time.sleep(.25)
                else:
                    raise RuntimeError("Card did not start")
                opened = send(client, card["supportedInterfaces"][0]["url"], QUERY)
                assert opened["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", opened
                ask = opened["status"]["message"]["metadata"]["ask"]
                item_id = opened["status"]["message"]["parts"][0]["text"].split(":", 1)[0]

                item = next(x for x in load() if x["name"] == item_id)
                assert item["category"] == "bookshelf" and item["material"] == "wood"
                assert item["capacity_books"] >= 40 and item["height_cm"] <= 75
                assert ask >= item["reserve"]
                completed = send(client, url, str(int(ask)), opened["id"])
                print(f"completed: {str(int(ask))} {completed['status']['state']}")
                assert completed["status"]["state"] == "TASK_STATE_COMPLETED", completed
                receipt = completed["artifacts"][0]["parts"][0]["data"]
                assert receipt["itemId"] == item["id"] and receipt["price"] >= item["reserve"]
                lines = [f"QUERY {QUERY}", f"CHOSEN {item['id']} reserve={item['reserve']} "
                         f"capacity={item['capacity_books']} height={item['height_cm']}",
                         f"ASK {ask} taskId={opened['id']}", f"RECEIPT {json.dumps(receipt)}"]
                (args.output / "trace.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
                print("\n".join(lines))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
