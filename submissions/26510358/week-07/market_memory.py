"""Personal outcomes keyed by registry name, with atomic cross-process writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
import re
from pathlib import Path
import tempfile
import time
from urllib.parse import urlparse


def confirmed_payment(pending):
    """The sequential buyer's kit payment must match its local contract.

    This is an observation of official kit output, not independent mandate
    verification. Peer quotes and seller-side ``settled`` lines are not payments.
    """
    payment = pending.get("payment") or {}
    price = pending.get("price")
    shop = urlparse(pending.get("url") or "")
    endpoint = urlparse(payment.get("endpoint") or "")
    return (pending.get("signature") == "verified"
            and pending.get("local_status") == "receipt_matched"
            and bool(pending.get("runtime_task"))
            and pending["runtime_task"] == pending.get("receipt_task")
            and pending.get("item") == pending.get("receipt_item")
            and type(price) in (int, float) and math.isfinite(price) and price > 0
            and price == payment.get("amount")
            and shop.scheme in {"http", "https"} and bool(shop.netloc)
            and (shop.scheme, shop.netloc) == (endpoint.scheme, endpoint.netloc)
            and bool(payment.get("ledger")))


class MarketMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        with self.path.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {"version": 1, "shops": {}}
            if data.get("version") != 1 or not isinstance(data.get("shops"), dict):
                raise ValueError("Invalid memory; preserve the file for diagnosis")
            yield data
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False) as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
                temporary = stream.name
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)

    def read(self):
        with self.transaction() as data:
            return json.loads(json.dumps(data))

    def ingest_kit_feedback(self, path):
        """Read our kit's authoritative signature verdict, never call market APIs.

        Seller-supplied public keys are not trusted. Only market_kit has the
        registry key; its verdict becomes personal memory on the next order.
        """
        path = Path(path)
        if not path.exists():
            return []
        imported = []
        with self.transaction() as data:
            offset = data.get("kit_log_offset", 0)
            pending = data.get("kit_pending", {})
            with path.open('rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = [stat.st_dev, stat.st_ino]
                if offset > stat.st_size or data.get("kit_log_identity", identity) != identity:
                    offset, pending = 0, {}
                stream.seek(offset)
                chunk = stream.read()
            end = chunk.rfind(b"\n") + 1
            for line in chunk[:end].decode("utf-8", errors="replace").splitlines():
                # Match the kit's own prefix, never a marker quoted inside an
                # untrusted remote message in a buyer/seller JSON trace.
                stamped = re.match(r"^\d{2}:\d{2}:\d{2} (.*)$", line)
                if not stamped:
                    continue
                entry = stamped.group(1)
                if entry.startswith("[order "):
                    order = re.fullmatch(r"\[order ([^]]+)\] ([^,]+), budget [^,]+, round (\d+)", entry)
                    pending = {"order": order[1], "item": order[2], "round": int(order[3])} if order else {}
                if entry.startswith('[buyer] {'):
                    try:
                        event = json.loads(entry[len("[buyer] "):])
                    except (ValueError, IndexError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("event") == "market_shop_selected":
                        pending["name"] = event.get("shop")
                        pending["url"] = event.get("url")
                    if event.get("event") == "trade_stage" and event.get("side") == "buyer" \
                            and event.get("stage") == "negotiation_started":
                        pending["runtime_task"] = event.get("task_id")
                    if event.get("event") in ("market_trade_observed", "purchase_completed", "purchase_receipt_matched"):
                        receipt = event.get("receipt") or {}
                        pending["receipt_task"] = receipt.get("task", receipt.get("taskId"))
                        pending["receipt_item"] = receipt.get("item")
                        if event.get("task_id"):
                            pending["runtime_task"] = event["task_id"]
                        pending["task"] = pending.get("runtime_task") or pending["receipt_task"]
                        if event.get("status"):
                            pending["local_status"] = event["status"]
                        pending["price"] = receipt.get("price")
                payment = re.fullmatch(r"\[acp\] paid (\d+) through (\S+), settled at ledger #(\d+)", entry)
                if payment and pending.get("signature") == "verified":
                    pending["payment"] = {"amount": int(payment[1]), "endpoint": payment[2], "ledger": payment[3]}
                if entry.startswith("[sign] "):
                    if "sig_seller ok" in line:
                        pending["signature"] = "verified"
                    elif "does not verify" in line:
                        pending["signature"] = "invalid"
                    elif "has no sig_seller" in line:
                        pending["signature"] = "missing"
                if entry.startswith("[market] ledger #") and pending.get("name"):
                    name, verdict = pending["name"], pending.get("signature")
                    shop = data["shops"].setdefault(name, {"trades": 0, "completed": 0, "failures": 0, "recent": []})
                    if verdict == "verified":
                        shop["signature_verified_by_kit"] = shop.get("signature_verified_by_kit", 0) + 1
                    elif verdict in {"invalid", "missing"}:
                        shop["signature_failures"] = shop.get("signature_failures", 0) + 1
                        shop["avoid_until"] = time.time() + (86400 if verdict == "invalid" else 1800)
                        shop["avoid_reason"] = "Kit reports " + verdict + " seller signature; incompatibility alone does not prove intent"
                    market_verified = verdict == "verified" and bool(re.search(r"\bverified True(?:,|$)", entry))
                    runtime_task = pending.get("runtime_task")
                    local_contract_ok = (not runtime_task or runtime_task == pending.get("receipt_task")) \
                        and pending.get("local_status") not in {"disputed", "unresolved", "error", "local_error", "unavailable", "no_deal", "untrusted"}
                    settlement_required = pending.get("round", 0) >= 3
                    settled = settlement_required and market_verified and local_contract_ok and confirmed_payment(pending)
                    verified = market_verified and local_contract_ok and (not settlement_required or settled)
                    ledger = re.search(r"ledger #(\d+)", entry)
                    feedback_id = ledger[1] if ledger else None
                    seen = shop.setdefault("verified_ledgers", [])
                    duplicate = feedback_id in seen or (settled and (
                        pending.get("order") in shop.get("settled_orders", [])
                        or pending["payment"]["ledger"] in shop.get("settlement_ledgers", [])))
                    if duplicate:
                        # Replayed feedback cannot promote another task or
                        # change the state of an already confirmed purchase.
                        pending = {}
                        continue
                    if not verified:
                        for row in reversed(shop.get("recent", [])):
                            if pending.get("task") and row.get("task") == pending["task"]:
                                row.update(status="unresolved", verified=False, ledger=feedback_id,
                                           order=pending.get("order"))
                                failure = ("local_contract_failed" if not local_contract_ok else
                                           "settlement_unconfirmed" if market_verified and settlement_required else
                                           "market_verification_failed")
                                row["issues"] = list(dict.fromkeys([*row.get("issues", []), failure]))
                                break
                    if verified and feedback_id:
                        seen.append(feedback_id)
                        shop["verified_completions"] = shop.get("verified_completions", 0) + 1
                        shop["last_verified_at"] = datetime.now(timezone.utc).isoformat()
                        product = shop.setdefault("verified_by_item", {}).setdefault(pending.get("item", "unknown"),
                            {"completed": 0})
                        product["completed"] += 1
                        if settled:
                            shop["round3_settlements"] = shop.get("round3_settlements", 0) + 1
                            shop.setdefault("settled_orders", []).append(pending["order"])
                            shop.setdefault("settlement_ledgers", []).append(pending["payment"]["ledger"])
                            settled_product = shop.setdefault("settled_by_item", {}).setdefault(
                                pending["item"], {"completed": 0})
                            settled_product["completed"] += 1
                        for row in reversed(shop.get("recent", [])):
                            if pending.get("task") and row.get("task") == pending["task"]:
                                row.update(status="completed", verified=True, ledger=feedback_id,
                                           order=pending.get("order"), round=pending.get("round"),
                                           settled=settled, settlement_ledger=(pending["payment"]["ledger"] if settled else None))
                                break
                    imported.append({"shop": name, "signature": verdict, "verified": verified,
                                     "task": pending.get("task"), "item": pending.get("item"),
                                     "task_linkage": "runtime" if runtime_task else "receipt_only",
                                     "receipt_task": pending.get("receipt_task"),
                                     "market_verified": market_verified, "local_contract_ok": local_contract_ok,
                                     "round": pending.get("round"), "settled": settled,
                                     "settlement_required": settlement_required, "duplicate": duplicate,
                                     "settlement_ledger": pending["payment"]["ledger"] if settled else None,
                                     "order": pending.get("order"), "ledger": feedback_id,
                                     "price": pending.get("price"), "market_result": line})
                    pending = {}
            data["kit_log_offset"] = offset + end
            data["kit_log_identity"] = identity
            data["kit_pending"] = pending
        return imported

    def visit(self, name, *, item, reason, owner=None):
        """A selected negotiation is a visit, independently of acceptance/settlement."""
        with self.transaction() as data:
            shop = data["shops"].setdefault(name, {"trades": 0, "completed": 0, "failures": 0, "recent": []})
            stamp = datetime.now(timezone.utc).isoformat()
            shop["visits"] = shop.get("visits", 0) + 1
            shop.setdefault("first_visit_at", stamp)
            shop["last_visit_at"] = stamp
            shop["last_selection"] = {"time": stamp, "item": item, "reason": reason[:500],
                                      "returning_verified": shop.get("verified_completions", 0) > 0}
            window = data.setdefault("selection_window", [])
            window.append({"shop": name, "owner": owner, "item": item, "time": stamp})
            data["selection_window"] = window[-60:]

    def record(self, name, *, item, budget, status, price=None, reason="", issues=None, signed=False,
               task=None, offer_count=None, response_seconds=None, penalize_shop=None, attribution="unknown"):
        with self.transaction() as data:
            shop = data["shops"].setdefault(name, {"trades": 0, "completed": 0, "failures": 0, "recent": []})
            shop["trades"] += 1
            if status == "receipt_matched":
                shop["accepted_receipts"] = shop.get("accepted_receipts", 0) + 1
            elif status == "completed":
                shop["completed"] += 1
            elif status == "local_error":
                shop["local_errors"] = shop.get("local_errors", 0) + 1
            elif status == "unresolved":
                shop["unresolved_attempts"] = shop.get("unresolved_attempts", 0) + 1
            elif penalize_shop is True or status in {"disputed", "untrusted"}:
                shop["failures"] += 1
                shop["attributed_shop_failures"] = shop.get("attributed_shop_failures", 0) + 1
            shop["recent"].append({"time": datetime.now(timezone.utc).isoformat(), "item": item,
                "budget": budget, "status": status, "price": price, "reason": reason[:500],
                "issues": issues or [], "signature_present": signed, "verified": False,
                "penalize_shop": penalize_shop, "attribution": attribution,
                "task": task, "offer_count": offer_count, "response_seconds": response_seconds})
            shop["recent"] = shop["recent"][-30:]
            # A factual receipt inconsistency is stronger evidence than a bad rating.
            if status == "disputed":
                shop["avoid_until"] = time.time() + 24 * 3600
            elif status == "untrusted":
                shop["avoid_until"] = time.time() + 24 * 3600
                shop["avoid_reason"] = reason[:500]


def rank_shops(shops, memory, item, budget, *, now=None):
    now = time.time() if now is None else now
    ranked = []
    for shop in shops:
        past = memory.get("shops", {}).get(shop["name"], {})
        if past.get("avoid_until", 0) > now:
            continue
        valid_reviews = [r for r in shop.get("reviews", []) if isinstance(r, dict)
                   and isinstance(r.get("stars"), (int, float)) and not isinstance(r["stars"], bool)
                   and 1 <= r["stars"] <= 5][:50]
        reviews, seen_reviewers = [], set()
        for index, review in enumerate(valid_reviews):
            owner, author = review.get("registry_author_owner"), review.get("author")
            if (isinstance(owner, str) and shop.get("registry_owner_verified") is True
                    and owner.casefold() == str(shop.get("registry_owner", "")).casefold()):
                continue
            identity = ("owner", owner.casefold()) if isinstance(owner, str) else (
                ("author", author) if isinstance(author, str) else ("unidentified", index))
            if identity in seen_reviewers:
                continue
            seen_reviewers.add(identity)
            reviews.append(review)
            if len(reviews) >= 20:
                break
        score = (sum(r["stars"] for r in reviews) + 8) / (len(reviews) + 2)
        round3 = isinstance(shop.get("current_round"), int) and shop["current_round"] >= 3
        verified = past.get("settled_by_item" if round3 else "verified_by_item", {}).get(item, {}).get("completed", 0)
        score += min(verified, 5) * 0.15
        score += min(past.get("round3_settlements" if round3 else "verified_completions", 0), 3) * 0.25
        recent = past.get("recent", [])[-5:]
        # Legacy generic errors have no attribution; they cannot prove shop fault.
        score -= sum(r.get("status") in {"error", "unavailable"} and r.get("penalize_shop") is True for r in recent) * 0.6
        score -= sum(r.get("status") == "disputed" for r in recent) * 2
        # Historical price is context for obtaining a fresh quote, not a current price advantage.
        ranked.append({**shop, "score": round(score, 3), "personal_memory": past,
            "review_sample_size": len(reviews),
            "reviews": [{"stars": r["stars"], "review": str(r.get("review", ""))[:250],
                         "round": r.get("round"), "author": r.get("author"),
                         "registry_author_owner": r.get("registry_author_owner")} for r in reviews[:5]]})
    return sorted(ranked, key=lambda s: (-s["score"], s["name"]))
