"""STEP 6: A2A buyer transport. Explicit manual offers only; no model decisions yet."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parent
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}


class BuyerError(RuntimeError):
    """Transport, protocol, or budget failure; not a successful purchase."""


class BuyerTransport:
    def __init__(self, url, item, budget, *, timeout=290, transport=None):
        if item not in ("lamp", "chair", "bookshelf"):
            raise ValueError("Unsupported item")
        if type(budget) is not int or budget <= 0:
            raise ValueError("Budget must be a positive integer")
        if not math.isfinite(timeout) or not 0 < timeout < 300:
            raise ValueError("Timeout must be positive and below 300 seconds")
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ValueError("Provide an absolute HTTP(S) seller base URL")
        self.base_url, self.item, self.budget = url.rstrip("/"), item, budget
        self.deadline = time.monotonic() + timeout
        self.endpoint = None
        self.card = None
        self.task = None
        self.receipt = None
        self.sequence = 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.log_dir = ROOT / "logs/buyer" / (stamp + "-" + uuid4().hex[:8])
        self.log_dir.mkdir(parents=True)
        self.client = httpx.Client(headers={"A2A-Version": "1.0"}, trust_env=False, transport=transport)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.client.close()
        metadata = {"scope": "A2A transport", "item": self.item, "budget": self.budget,
                    "task_id": self.task_id, "state": self.state, "receipt": self.receipt,
                    "status": "error" if exc_value else "ok", "error": str(exc_value) if exc_value else None}
        (self.log_dir / "session.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    @property
    def task_id(self):
        return self.task["id"] if self.task else None

    @property
    def state(self):
        return self.task["status"]["state"] if self.task else None

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise BuyerError("Buyer time limit exceeded")
        return seconds

    def _request(self, method, url, body=None):
        self.sequence += 1
        prefix = f"{self.sequence:02d}"
        request = {"method": method, "url": str(url), "headers": {"A2A-Version": "1.0"}, "body": body}
        (self.log_dir / f"{prefix}.request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        event = {}
        try:
            response = self.client.request(method, url, json=body, timeout=min(100, self.remaining()))
            (self.log_dir / f"{prefix}.response.txt").write_bytes(response.content)
            event["http_status"] = response.status_code
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, BuyerError) as error:
            event["error"] = str(error)
            raise BuyerError(f"Request failed: {error}. Logs: {self.log_dir}") from error
        finally:
            (self.log_dir / f"{prefix}.http.json").write_text(json.dumps(event, indent=2), encoding="utf-8")

    def discover(self):
        card = self._request("GET", self.base_url + "/.well-known/agent-card.json")
        if not isinstance(card, dict):
            raise BuyerError("Agent Card must be a JSON object")
        interfaces = card.get("supportedInterfaces", [])
        chosen = next((entry for entry in interfaces if isinstance(entry, dict)
                       and entry.get("protocolBinding") == "JSONRPC"
                       and str(entry.get("protocolVersion")) == "1.0"), None)
        if chosen is None:
            raise BuyerError("Seller has no JSON-RPC 1.0 interface")
        endpoint = chosen.get("url")
        if not isinstance(endpoint, str):
            raise BuyerError("Agent Card interface URL is missing")
        parsed = httpx.URL(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise BuyerError("Agent Card must advertise an absolute HTTP(S) URL")
        self.card, self.endpoint = card, endpoint
        return card

    def _rpc(self, method, params):
        if self.endpoint is None:
            self.discover()
        request_id = str(uuid4())
        payload = self._request("POST", self.endpoint, {"jsonrpc": "2.0", "id": request_id,
                                                       "method": method, "params": params})
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
            raise BuyerError("Invalid JSON-RPC response envelope or request ID")
        if "error" in payload:
            raise BuyerError("A2A error: " + json.dumps(payload["error"]))
        result = payload.get("result")
        task = result.get("task", result) if isinstance(result, dict) else None
        if (not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]
                or not isinstance(task.get("status"), dict)
                or not isinstance(task["status"].get("state"), str)):
            raise BuyerError("Expected a task with id and status")
        if self.task_id is not None and task["id"] != self.task_id:
            raise BuyerError("Seller changed taskId during the negotiation")
        if self.task and self.task.get("contextId") != task.get("contextId"):
            raise BuyerError("Seller changed contextId during the negotiation")
        self.task = task
        if self.state == "TASK_STATE_COMPLETED":
            receipts = [part["data"] for artifact in task.get("artifacts", [])
                        for part in artifact.get("parts", [])
                        if isinstance(part.get("data"), dict) and "price" in part["data"]]
            if len(receipts) != 1:
                raise BuyerError("Completed task must have one receipt with a numeric price")
            receipt = receipts[0]
            price = receipt["price"]
            if type(price) not in (int, float) or not math.isfinite(price) or not 0 < price <= self.budget:
                raise BuyerError("Receipt price is invalid or exceeds budget")
            if "item" in receipt and receipt["item"] != self.item:
                raise BuyerError("Receipt item differs from the requested item")
            self.receipt = receipt
        return task

    def open(self):
        if self.task is not None:
            raise BuyerError("This buyer already has a task")
        return self._rpc("SendMessage", {"message": {"role": "ROLE_USER", "messageId": str(uuid4()),
                         "parts": [{"text": f"I want to buy a {self.item}"}]}})

    def offer(self, price):
        if self.state != "TASK_STATE_INPUT_REQUIRED":
            raise BuyerError("Offers require an open input-required task")
        if type(price) is not int or not 0 < price <= self.budget:
            raise BuyerError("Offer must be a positive integer within budget")
        message = {"role": "ROLE_USER", "messageId": str(uuid4()), "taskId": self.task_id,
                   "parts": [{"text": str(price)}]}
        if self.task.get("contextId"):
            message["contextId"] = self.task["contextId"]
        return self._rpc("SendMessage", {"message": message})

    def get_task(self):
        if not self.task_id:
            raise BuyerError("No task to retrieve")
        return self._rpc("GetTask", {"id": self.task_id})

    def cancel(self):
        if not self.task_id or self.state in TERMINAL:
            raise BuyerError("No active task to cancel")
        return self._rpc("CancelTask", {"id": self.task_id})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", choices=["lamp", "chair", "bookshelf"], required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--manual", action="store_true", required=True,
                        help="Exercise transport using explicit offers, without a model")
    parser.add_argument("--offer", action="append", type=int, default=[])
    parser.add_argument("--cancel", action="store_true", help="Cancel if still active after the supplied offers")
    args = parser.parse_args()
    buyer = None
    try:
        with BuyerTransport(args.url, args.item, args.budget) as buyer:
            task = buyer.open()
            print(json.dumps(task, ensure_ascii=False), flush=True)
            for offer in args.offer:
                if buyer.state in TERMINAL:
                    break
                print(json.dumps(buyer.offer(offer), ensure_ascii=False), flush=True)
            if args.cancel and buyer.state not in TERMINAL:
                print(json.dumps(buyer.cancel(), ensure_ascii=False), flush=True)
    except (BuyerError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    finally:
        if buyer is not None:
            print(f"Buyer logs: {buyer.log_dir}", file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
