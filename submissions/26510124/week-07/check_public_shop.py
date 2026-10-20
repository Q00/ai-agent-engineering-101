"""Exercise the public card, multi-turn A2A transport, and final receipt.

This fixed test counterpart accepts the seller's quote (up to --budget).
It does not submit a market result or write a review.
"""
import argparse
import json
from pathlib import Path
import time
import uuid

import httpx

from buyer_agent import last_asking_price, message_text


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url")
    ap.add_argument("--item", default="lamp")
    ap.add_argument("--budget", type=int, default=250)
    args = ap.parse_args()
    directory = Path(__file__).resolve().parent
    base = (args.url or (directory / "agent-market/shop-url").read_text().strip()).rstrip("/")
    logfile = directory / "logs" / f"public-trade-check-{uuid.uuid4().hex[:10]}.log"
    print(logfile)
    with logfile.open("x") as out, httpx.Client(timeout=100) as client:
        def record(value):
            line = json.dumps(value, ensure_ascii=False)
            out.write(line + "\n")
            out.flush()
            print(line, flush=True)

        try:
            response = client.get(base + "/.well-known/agent-card.json")
            response.raise_for_status()
            card = response.json()
            endpoint = next(i["url"] for i in card["supportedInterfaces"] if i["protocolBinding"] == "JSONRPC")
            record({"card": card})
            task_id, offered = None, None
            text = f"I want to buy a {args.item}"
            for _ in range(4):
                message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
                if task_id:
                    message["taskId"] = task_id
                body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage",
                        "params": {"message": message}}
                start = time.monotonic()
                response = client.post(endpoint, headers={"A2A-Version": "1.0"}, json=body)
                record({"request": body, "seconds": round(time.monotonic() - start, 3),
                        "status": response.status_code, "body": response.text})
                response.raise_for_status()
                reply = response.json()
                assert reply.get("id") == body["id"] and "error" not in reply, reply
                task = reply["result"]["task"]
                assert task_id is None or task["id"] == task_id, "taskId changed"
                task_id = task["id"]
                if task["status"]["state"] == "TASK_STATE_COMPLETED":
                    receipt = next(p["data"] for a in task["artifacts"] for p in a["parts"] if "data" in p)
                    assert receipt["price"] == offered and receipt["item"] == args.item, receipt
                    record({"PASS": True, "receipt": receipt, "market_result_submitted": False})
                    return
                assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", task
                quote = last_asking_price(message_text(task))
                assert quote is not None and quote.is_integer() and 0 < quote <= args.budget, quote
                offered = int(quote)
                text = str(offered)
            raise AssertionError("seller did not complete after accepting its quotes")
        except Exception as exc:
            record({"PASS": False, "error": f"{type(exc).__name__}: {exc}"})
            raise


if __name__ == "__main__":
    main()
