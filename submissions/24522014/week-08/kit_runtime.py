"""Local adapter: close failed/expired checkouts and report outcomes to our seller.

The course kit's signatures, mandates and settlement checks are retained unchanged.
"""

import os
import time
import asyncio

from market_state import read, write, task_file

OFFER_SECONDS = 600


def install(namespace):
    g = namespace["main"].__globals__
    original = g["AcpShop"]
    response = g["JSONResponse"]

    class GuardedACP(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.create_lock = asyncio.Lock()

        def event(self, task, status):
            if self.md:
                write(task_file(self.md, "checkout-events", task),
                      {"task": task, "status": status, "at": time.time()})

        def expired(self, task):
            if not self.md:
                return False
            offer = read(task_file(self.md, "signed-offers", task), {})
            return bool(offer and time.time() >= offer["expires_at"])

        async def create(self, body, public):
            async with self.create_lock:
                return await self.create_serialized(body, public)

        async def create_serialized(self, body, public):
            receipt = body.get("receipt")
            task = receipt.get("task") if isinstance(receipt, dict) else None
            if task is not None and not isinstance(task, str):
                return response({"error": "invalid receipt task"}, status_code=400)
            if task and self.md:
                event = read(task_file(self.md, "checkout-events", task), {})
                if self.expired(task) or event.get("status") in ("canceled", "refused", "expired", "completed", "processing", "uncertain"):
                    # Never overwrite a pending completion's state during a concurrent retry.
                    if event.get("status") not in ("processing", "uncertain", "completed"):
                        self.event(task, "expired" if self.expired(task) else event["status"])
                    return response({"error": "receipt closed or expired; request a new quote"}, status_code=409)
            result = await super().create(body, public)
            if task and result.status_code == 201:
                if self.expired(task):
                    for session in self.sessions.values():
                        if session.get("task") == task:
                            session["status"] = "canceled"
                    self.event(task, "expired")
                    return response({"error": "receipt expired during checkout"}, status_code=409)
                self.event(task, "ready")
            return result

        async def handle(self, req, path, body, public):
            parts = path.split("/")
            session = self.sessions.get(parts[2]) if len(parts) >= 3 else None
            if session and len(parts) >= 4:
                task = session["task"]
                if parts[3] == "complete" and session["status"] == "ready_for_payment":
                    event = read(task_file(self.md, "checkout-events", task), {}) if self.md else {}
                    if event.get("status") in ("processing", "uncertain"):
                        return response({"error": "payment outcome unresolved"}, status_code=409)
                    if self.expired(task):
                        session["status"] = "canceled"
                        self.event(task, "expired")
                        return response({"error": "checkout expired"}, status_code=409)
                    self.event(task, "processing")
                    try:
                        result = await super().handle(req, path, body, public)
                    except BaseException:
                        # Uncertain settlement is not proof of failure: retain the hold.
                        self.event(task, "uncertain")
                        raise
                    if result.status_code == 200 and session["status"] == "completed":
                        self.event(task, "completed")
                    elif result.status_code == 402:
                        session["status"] = "canceled"
                        self.event(task, "refused")
                    else:
                        self.event(task, "uncertain")
                    return result
                if parts[3] == "cancel" and session["status"] != "completed":
                    event = read(task_file(self.md, "checkout-events", task), {}) if self.md else {}
                    if event.get("status") in ("processing", "uncertain"):
                        return response({"error": "payment outcome unresolved"}, status_code=409)
                    result = await super().handle(req, path, body, public)
                    self.event(task, "canceled")
                    return result
            return await super().handle(req, path, body, public)

    g["AcpShop"] = GuardedACP
    os.environ["MARKET_LEASE_GUARD"] = "1"

