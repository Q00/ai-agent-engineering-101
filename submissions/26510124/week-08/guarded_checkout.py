"""ACP lease enforcement around the unchanged, hash-verified classroom kit.

Only receipts recorded by this seller can create or complete a checkout.  An
unpaid receipt can expire because every later checkout route checks the same
durable lease.  A settlement whose result is unknown is never released by time.
"""
import asyncio
import json
import time
import uuid

from starlette.responses import JSONResponse

from feedback import append_event
from receipt_leases import LeaseError, ReceiptLeaseStore
from vendor import market_kit_v4 as kit


def explicit_validation_refusal(status, result):
    """Recognize only a structured refusal of this single settlement request.

    A timeout, proxy/server failure, plain error string or evidence of a possible
    settlement is insufficient. Market.call does not replay application requests.
    """
    if (status not in {400, 403, 404, 409, 422} or not isinstance(result, dict)
            or "ledger" in result or "mandate_receipt" in result):
        return False
    checks = result.get("checks")
    return (isinstance(checks, dict) and bool(checks)
            and all(isinstance(key, str) and type(value) is bool for key, value in checks.items())
            and any(value is False for value in checks.values()))


class GuardedAcpShop(kit.AcpShop):
    def __init__(self, key, market, md=None):
        if md is None:
            raise ValueError("Guarded checkout requires the seller's market directory")
        super().__init__(key, market, md)
        self.leases = ReceiptLeaseStore(md)
        self.leases.initialize()
        self.gate = asyncio.Lock()
        self.runtime_id = uuid.uuid4().hex
        self.started_at = time.time()

    def event(self, task, unit, stage, *, ledger=None):
        """Append identifiers only; round is obtained by matching seller receipts.

        Logging cannot affect payment/lease state. No mandates, signatures,
        checkout tokens, customer details or prices enter this stream.
        """
        try:
            append_event(self.leases.folder / "checkout-events.jsonl",
                         {"time": time.time(), "task": task, "unit": unit,
                          "stage": stage, "ledger": ledger})
        except OSError:
            kit.log("[acp] could not append checkout diagnostic event")

    def card(self, public):
        return {**super().card(public), "x_receipt_guard": {
            "version": 1, "runtime_id": self.runtime_id, "started_at": self.started_at}}

    @staticmethod
    def refused(message, status=409):
        return JSONResponse({"error": message}, status_code=status)

    def _forget(self, task, before):
        for sid, session in list(self.sessions.items()):
            if sid not in before and session.get("task") == task:
                self.sessions.pop(sid, None)

    def _sweep_sessions(self):
        held = self.leases.sweep()
        for session in self.sessions.values():
            if (session.get("status") == "ready_for_payment"
                    and held.get(session.get("unit")) != session["task"]):
                # The upstream picker skips ready sessions; retire expired
                # ones as well as the durable lease before selecting stock.
                session["status"] = "expired"

    async def create(self, b, public):
        async with self.gate:
            return await self._create(b, public)

    async def _create(self, b, public):
        if not isinstance(b, dict) or not isinstance(b.get("receipt"), dict):
            return self.refused("receipt must be an object", 400)
        rec = b["receipt"]
        task = rec.get("task")
        items = b.get("line_items")
        if (not isinstance(task, str) or not task or not kit.verify_seller(self.pub, rec)
                or type(rec.get("price")) is not int):
            return self.refused("the receipt is not signed by this shop", 400)
        if (not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict)
                or items[0].get("id") != rec.get("item") or items[0].get("quantity") != 1):
            return self.refused("line_items must contain the agreed item once", 400)
        before = set(self.sessions)
        try:
            self._sweep_sessions()
            lease = self.leases.authorize(task, receipt=rec)
            if lease.get("session_id"):
                # A process restart loses the session's private state. Do not
                # silently give the receipt a second checkout; its lease expires.
                return self.refused("this receipt already has a checkout session")
            own = dict(lease["receipt"], unit=lease["unit"])
            self.terms[task] = own
            response = await super().create(b, public)
            if response.status_code != 201:
                return response
            session = json.loads(response.body)
            self.leases.authorize(task, receipt=rec)
            if session.get("unit") != lease["unit"]:
                raise LeaseError("Checkout did not select the reserved unit")
            self.leases.checkout(task, session["id"])
            self.event(task, lease["unit"], "checkout_created")
            return response
        except LeaseError as exc:
            self._forget(task, before)
            return self.refused(str(exc))
        except asyncio.CancelledError:
            self._forget(task, before)
            raise
        except Exception as exc:
            self._forget(task, before)
            kit.log(f"[acp] guarded checkout unavailable: {type(exc).__name__}")
            return self.refused("checkout is temporarily unavailable; no payment was attempted", 503)

    async def handle(self, req, path, body, public):
        if path == ".well-known/acp.json":
            return JSONResponse(self.card(public))
        try:
            payload = json.loads(body or b"{}")
        except (ValueError, TypeError):
            return self.refused("body is not JSON", 400)
        if not isinstance(payload, dict):
            return self.refused("body must be an object", 400)
        parts = path.split("/")[1:]
        if parts[:1] != ["checkout_sessions"]:
            return self.refused("unknown ACP path", 404)
        if len(parts) == 1:
            if req.method != "POST":
                return self.refused("method not allowed", 405)
            return await self.create(payload, public)
        if len(parts) not in (2, 3):
            return self.refused("unknown ACP path", 404)
        async with self.gate:
            try:
                self._sweep_sessions()
            except (OSError, LeaseError):
                return self.refused("could not read checkout state", 503)
            session = self.sessions.get(parts[1])
            if session is None:
                return self.refused("no such checkout session", 404)
            if len(parts) == 2:
                if req.method != "GET":
                    return self.refused("method not allowed", 405)
                return JSONResponse(session)
            if req.method != "POST":
                return self.refused("method not allowed", 405)
            try:
                if parts[2] == "cancel":
                    self.leases.cancel(session["task"], session["id"])
                    session["status"] = "canceled"
                    self.event(session["task"], session["unit"], "checkout_cancelled")
                    return JSONResponse(session)
                if parts[2] != "complete":
                    return self.refused("unknown ACP path", 404)
                if session["status"] == "completed":
                    return JSONResponse(session)
                self.leases.authorize(session["task"], session_id=session["id"])
                pay = payload.get("payment_data")
                if (not isinstance(pay, dict) or not isinstance(pay.get("order"), str)
                        or not isinstance(pay.get("checkout_mandate"), dict)
                        or not isinstance(pay.get("payment_mandate"), dict)):
                    return self.refused("payment_data is incomplete", 400)
                self.leases.begin_settle(session["task"], session["id"])
                session["status"] = "processing"
                self.event(session["task"], session["unit"], "settlement_started")
            except LeaseError as exc:
                return self.refused(str(exc))
            except OSError:
                return self.refused("could not save payment state; no payment was attempted", 503)
            return await self._complete(session, pay)

    def _uncertain(self, session):
        session["status"] = "payment_uncertain"
        self.event(session["task"], session["unit"], "settlement_uncertain")
        try:
            self.leases.finish(session["task"], "uncertain")
        except (OSError, LeaseError):
            # A prior durable transition may already have committed. Never
            # overwrite it or release another reservation after a write error.
            kit.log("[acp] could not record uncertain payment; inspect durable receipt state")

    async def _complete(self, session, pay):
        try:
            response = await asyncio.to_thread(
                self.market.call, "POST", "/api/settle", "shop",
                json={"order": pay["order"], "checkout_jwt": session["checkout_jwt"],
                      "checkout_mandate": pay["checkout_mandate"], "payment_mandate": pay["payment_mandate"]})
            result = response.json()
            if explicit_validation_refusal(response.status_code, result):
                self.leases.refuse(session["task"], session["id"])
                session["status"] = "canceled"
                self.event(session["task"], session["unit"], "settlement_refused")
                failed = [name for name, valid in result["checks"].items() if not valid]
                kit.log(f"[acp] settle refused: {failed}; receipt revoked")
                return JSONResponse({"error": "payment refused by the market", "market": {
                    "checks": result["checks"]}}, status_code=402)
            receipt = result.get("mandate_receipt") if isinstance(result, dict) else None
            if (response.status_code != 200 or not isinstance(receipt, dict)
                    or receipt.get("unit") != session["unit"] or not result.get("ledger")):
                # Outside the explicit refusal contract, a failed response does
                # not prove no commit. Preserve the unit for review.
                self._uncertain(session)
                kit.log(f"[acp] settlement not confirmed: HTTP {response.status_code}; hold retained")
                return self.refused("payment was not confirmed; stock remains reserved", 502)
            self.leases.finish(session["task"], "completed")
            session["status"] = "completed"
            self.event(session["task"], session["unit"], "settlement_confirmed", ledger=result["ledger"])
            session["order"] = {"status": "confirmed", "mandate_receipt": receipt}
        except asyncio.CancelledError:
            self._uncertain(session)
            raise
        except Exception as exc:
            self._uncertain(session)
            kit.log(f"[acp] payment result unknown: {type(exc).__name__}; hold retained")
            return self.refused("payment result is unknown; stock remains reserved", 503)
        # Cache refresh failure must not turn a confirmed payment into a second
        # request. The kit's regular inventory sync will retry the refresh.
        try:
            await asyncio.to_thread(kit.refresh_inventory, self.market, self.md)
        except Exception as exc:
            kit.log(f"[acp] confirmed payment; inventory refresh deferred: {type(exc).__name__}")
        kit.log(f"[acp] settled {receipt.get('name') or session['line_items'][0]['id']} "
                f"at {session['totals'][0]['amount']}, ledger #{result['ledger']}, "
                f"unit {session['unit']} grade {receipt.get('grade')}")
        return JSONResponse(session)
