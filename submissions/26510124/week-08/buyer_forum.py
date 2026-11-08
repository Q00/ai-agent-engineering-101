"""Buyer-role settlement reports without model calls or vendor outbox races.

The review hook queues sanitized confirmed receipts. The kit's sync loop drains
this independent spool at most once per 10 minutes. A durable in-flight marker
is never automatically replayed after a crash or an unknown transport outcome.
"""
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from receipt_leases import durable_json

INTERVAL_SECONDS = 600
FRESH_SECONDS = 6 * 3600
MAX_PENDING = 64
MAX_SEEN = 65536
MAX_HISTORY = 64
MAX_STATE_BYTES = 2_000_000
SKU = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
FIELDS = {"ledger", "sku", "price", "grade", "kind", "warranty_hours", "observed_at"}


class BuyerForumError(ValueError):
    pass


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def positive_id(value):
    return type(value) is int and 0 < value < 2**63


def _entry_valid(entry):
    return (isinstance(entry, dict) and set(entry) == FIELDS
            and positive_id(entry["ledger"]) and isinstance(entry["sku"], str) and bool(SKU.fullmatch(entry["sku"]))
            and type(entry["price"]) is int and 0 < entry["price"] < 10**12
            and entry["grade"] in ("A", "B", "C") and entry["kind"] in ("goods", "food")
            and (entry["warranty_hours"] is None or number(entry["warranty_hours"])
                 and 0 <= entry["warranty_hours"] <= 8760)
            and number(entry["observed_at"]) and entry["observed_at"] > 0)


def _read(path, limit=MAX_STATE_BYTES):
    if path.is_symlink():
        raise BuyerForumError("Forum state must not be a symbolic link")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise BuyerForumError("Forum state exceeds its size limit")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise BuyerForumError("Forum state is not valid JSON") from exc


def _round_ready(md):
    path = md / "inventory.json"
    if not path.exists():
        return True  # A buyer-only kit has no inventory; the hook still validates R3 fields.
    value = _read(path, 8_000_000)
    return isinstance(value, dict) and type(value.get("round")) is int and value["round"] == 3


def _extract(deal, assessment, now):
    if not isinstance(deal, dict) or not isinstance(assessment, dict):
        raise BuyerForumError("missing settled deal")
    settled, receipt, order = deal.get("settled"), deal.get("receipt"), deal.get("order")
    if not all(isinstance(value, dict) for value in (settled, receipt, order)):
        raise BuyerForumError("missing settlement receipt")
    if "ok" in settled and settled["ok"] is not True:
        raise BuyerForumError("settlement was not confirmed")
    if (type(order.get("round")) is not int or order["round"] != 3
            or "round" in settled and (type(settled["round"]) is not int or settled["round"] != 3)):
        raise BuyerForumError("settlement is not from round 3")
    if settled.get("sku") != receipt.get("item"):
        raise BuyerForumError("settled SKU differs from agreed item")
    if ("order" in settled and settled["order"] != order.get("order")):
        raise BuyerForumError("settlement belongs to another order")
    # Some receipts name the shop, others use its URL. Compare identities only
    # when both sides provide the same representation; never publish either.
    seller, shop = receipt.get("shop"), settled.get("shop")
    if (not isinstance(seller, str) or not seller or not isinstance(shop, str) or not shop):
        raise BuyerForumError("settled shop identity is unavailable")
    if (seller.startswith(("https://", "http://")) == shop.startswith(("https://", "http://"))
            and seller.rstrip("/") != shop.rstrip("/")):
        raise BuyerForumError("settled shop differs from agreed shop")
    if "shop_name" in settled and settled["shop_name"] != seller:
        raise BuyerForumError("settled shop name differs from agreed shop")
    price = receipt.get("price")
    if not number(price) or price <= 0 or price != int(price):
        raise BuyerForumError("invalid settled price")
    if any(key in settled and (not number(settled[key]) or settled[key] != price) for key in ("price", "amount")):
        raise BuyerForumError("settled price differs from agreed price")
    observed = assessment.get("reviewed_at")
    if not number(observed) or not 0 <= now - observed <= FRESH_SECONDS:
        raise BuyerForumError("settlement assessment is not fresh")
    for key in ("ts", "settled_at"):
        if key in settled:
            if not number(settled[key]) or not 0 <= now - settled[key] <= FRESH_SECONDS:
                raise BuyerForumError("settlement timestamp is not fresh")
            observed = min(observed, settled[key])
    result = {"ledger": settled.get("ledger"), "sku": settled.get("sku"), "price": int(price),
              "grade": settled.get("grade"), "kind": order.get("kind"),
              "warranty_hours": settled.get("warranty_hours"), "observed_at": observed}
    if not _entry_valid(result):
        raise BuyerForumError("settlement lacks valid public terms")
    return result


class Spool:
    def __init__(self, md):
        self.md = Path(md)
        self.path = self.md / "buyer-forum-spool.json"

    @contextmanager
    def lock(self, *, drain=False):
        self.md.mkdir(parents=True, exist_ok=True)
        path = self.md / ("buyer-forum-drain.lock" if drain else "buyer-forum-spool.lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "a+") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (fcntl.LOCK_NB if drain else 0))
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def load(self):
        if not self.path.exists():
            return {"version": 1, "pending": [], "seen": [], "attempt": None,
                    "next_send_at": 0, "last_content": None, "history": []}
        state = _read(self.path)
        if (not isinstance(state, dict) or set(state) != {
                "version", "pending", "seen", "attempt", "next_send_at", "last_content", "history"}
                or type(state["version"]) is not int or state["version"] != 1
                or not isinstance(state["pending"], list) or len(state["pending"]) > MAX_PENDING
                or not all(_entry_valid(entry) for entry in state["pending"])
                or not isinstance(state["seen"], list) or len(state["seen"]) > MAX_SEEN
                or not all(positive_id(seq) for seq in state["seen"])
                or len(set(state["seen"])) != len(state["seen"])
                or not number(state["next_send_at"]) or state["next_send_at"] < 0
                or state["last_content"] is not None and not re.fullmatch(r"[0-9a-f]{64}", str(state["last_content"]))
                or not isinstance(state["history"], list) or len(state["history"]) > MAX_HISTORY):
            raise BuyerForumError("Invalid buyer forum spool; refusing to reset deduplication")
        pending = [entry["ledger"] for entry in state["pending"]]
        if len(set(pending)) != len(pending) or not set(pending).issubset(state["seen"]):
            raise BuyerForumError("Forum evidence is missing its deduplication record")
        attempt = state["attempt"]
        if attempt is not None:
            if (not isinstance(attempt, dict) or set(attempt) != {"id", "entries", "state", "started_at", "fingerprint"}
                    or not isinstance(attempt["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", attempt["id"])
                    or attempt["state"] not in ("inflight", "retry")
                    or not number(attempt["started_at"]) or attempt["started_at"] <= 0
                    or not isinstance(attempt["entries"], list) or not 1 <= len(attempt["entries"]) <= 3
                    or not all(_entry_valid(entry) for entry in attempt["entries"])
                    or attempt["fingerprint"] != fingerprint(attempt["entries"])
                    or not {entry["ledger"] for entry in attempt["entries"]}.issubset(state["seen"])):
                raise BuyerForumError("Invalid forum attempt; refusing to resend")
        return state

    def save(self, state):
        if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_STATE_BYTES:
            raise BuyerForumError("Forum spool reached its size limit")
        durable_json(self.path, state)


def _terms(entry):
    return {key: entry[key] for key in ("sku", "price", "grade", "kind", "warranty_hours")}


def fingerprint(entries):
    terms = sorted((_terms(entry) for entry in entries), key=lambda row: json.dumps(row, sort_keys=True))
    return hashlib.sha256(json.dumps(terms, sort_keys=True).encode()).hexdigest()


def payload(entries):
    lines = ["[구매 정산 기록] 실제 결제된 거래의 조건입니다."]
    for entry in entries:
        details = f"{entry['sku']}: {entry['price']:,}원 · 정산 등급 {entry['grade']}"
        if entry["kind"] == "goods":
            warranty = entry["warranty_hours"]
            details += " · 보증 미기재" if warranty is None else f" · 보증 {warranty:g}시간"
        lines.append(details + f" · 원장 #{entry['ledger']}")
    lines.append("정산 시점의 기록이며 이후 상태나 미래 품질을 보장하지 않습니다.")
    return {"text": "\n".join(lines), "refs": [entry["ledger"] for entry in entries], "thread": None}


def _prune_pending(state, now):
    protected = {entry["ledger"] for entry in (state["attempt"] or {}).get("entries", [])}
    keep = [entry for entry in state["pending"] if entry["ledger"] in protected
            or 0 <= now - entry["observed_at"] <= FRESH_SECONDS]
    preserved = [entry for entry in keep if entry["ledger"] in protected]
    others = sorted((entry for entry in keep if entry["ledger"] not in protected),
                    key=lambda entry: (entry["observed_at"], entry["ledger"]))
    state["pending"] = preserved + others[-(MAX_PENDING - len(preserved)):]


def queue_confirmed_deal(root, deal, assessment, *, now=None):
    """Queue only public confirmed terms; never persist raw deal/assessment data."""
    now = time.time() if now is None else now
    md = Path(root) / ".market"
    try:
        entry = _extract(deal, assessment, now)
        if not _round_ready(md):
            return {"status": "wrong_round"}
    except BuyerForumError as exc:
        return {"status": "invalid", "reason": str(exc)}
    spool = Spool(md)
    with spool.lock():
        state = spool.load()
        if entry["ledger"] in state["seen"]:
            return {"status": "duplicate", "ledger": entry["ledger"]}
        if len(state["seen"]) >= MAX_SEEN:
            return {"status": "capacity"}  # Never evict deduplication and accidentally repost old evidence.
        state["seen"].append(entry["ledger"])
        state["pending"].append(entry)
        _prune_pending(state, now)
        spool.save(state)
    return {"status": "queued", "ledger": entry["ledger"]}


def _close_attempt(state, status, now, *, http_status=None, post_id=None):
    attempt = state["attempt"]
    refs = [entry["ledger"] for entry in attempt["entries"]]
    state["pending"] = [entry for entry in state["pending"] if entry["ledger"] not in refs]
    row = {"id": attempt["id"], "refs": refs, "status": status, "at": now}
    if type(http_status) is int:
        row["http_status"] = http_status
    if positive_id(post_id):
        row["post_id"] = post_id
    state["history"] = (state["history"] + [row])[-MAX_HISTORY:]
    if status == "sent":
        state["last_content"] = attempt["fingerprint"]
    state["attempt"] = None
    state["next_send_at"] = now + INTERVAL_SECONDS
    return {"status": status, "refs": refs}


def _backoff(response, now):
    delays = [INTERVAL_SECONDS]
    try:
        data = response.json()
        if isinstance(data, dict):
            if number(data.get("wait")):
                delays.append(data["wait"])
            if number(data.get("opens_at")):
                delays.append(data["opens_at"] - now)
    except Exception:
        pass
    value = response.headers.get("Retry-After", "")
    try:
        delays.append(float(value))
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            delays.append(stamp.timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            pass
    return now + min(86400, max(delay for delay in delays if number(delay)))


def drain_buyer_forum(market, md, *, now=None):
    """Send one pending buyer report; never use a shop token or retry unknown sends."""
    now = time.time() if now is None else now
    spool = Spool(md)
    if not spool.path.exists():
        return {"status": "empty"}
    token = getattr(market, "token", None)
    if not isinstance(token, dict) or not isinstance(token.get("buyer"), str) or not token["buyer"]:
        return {"status": "no_buyer_token"}
    if not _round_ready(spool.md):
        return {"status": "wrong_round"}
    # Different locks keep a slow HTTP response from blocking the review hook.
    with spool.lock(drain=True) as acquired:
        if not acquired:
            return {"status": "busy"}
        with spool.lock():
            state = spool.load()
            attempt = state["attempt"]
            if attempt and attempt["state"] == "inflight":
                result = _close_attempt(state, "uncertain", now)
                spool.save(state)
                return result  # Previous sender vanished; it may already have posted.
            if now < state["next_send_at"]:
                return {"status": "throttled"}
            previous_pending = list(state["pending"])
            _prune_pending(state, now)
            if attempt and any(not 0 <= now - entry["observed_at"] <= FRESH_SECONDS for entry in attempt["entries"]):
                result = _close_attempt(state, "expired", now)
                spool.save(state)
                return result
            if attempt is None:
                choices, signatures = [], set()
                for entry in sorted(state["pending"], key=lambda item: (item["observed_at"], item["ledger"]), reverse=True):
                    signature = fingerprint([entry])
                    if signature not in signatures:
                        choices.append(entry)
                        signatures.add(signature)
                    if len(choices) == 3:
                        break
                if not choices:
                    if state["pending"] != previous_pending:
                        spool.save(state)
                    return {"status": "empty"}
                signature = fingerprint(choices)
                if signature == state["last_content"]:
                    state["pending"] = [entry for entry in state["pending"] if fingerprint([entry]) not in signatures]
                    spool.save(state)
                    return {"status": "unchanged"}
                attempt = {"id": uuid.uuid4().hex, "entries": choices, "state": "inflight",
                           "started_at": now, "fingerprint": signature}
                state["attempt"] = attempt
            else:
                attempt.update(state="inflight", started_at=now)
            spool.save(state)  # Must reach durable storage before any POST.
            attempt_id = attempt["id"]
            body = payload(attempt["entries"])
        try:
            response = market.call("POST", "/api/forum", "buyer", json=body, timeout=5)
        except BaseException as exc:
            with spool.lock():
                state = spool.load()
                if not state["attempt"] or state["attempt"]["id"] != attempt_id:
                    raise BuyerForumError("Forum attempt changed while sending") from exc
                result = _close_attempt(state, "uncertain", now)
                spool.save(state)
            if not isinstance(exc, Exception):
                raise
            return result
        with spool.lock():
            state = spool.load()
            if not state["attempt"] or state["attempt"]["id"] != attempt_id:
                raise BuyerForumError("Forum attempt changed while sending")
            status = response.status_code
            if status == 429:
                state["attempt"]["state"] = "retry"
                state["next_send_at"] = _backoff(response, now)
                result = {"status": "rate_limited", "retry_at": state["next_send_at"]}
            elif status in (200, 201):
                try:
                    data = response.json()
                    post_id = data.get("id") if isinstance(data, dict) else None
                except Exception:
                    post_id = None
                result = _close_attempt(state, "sent", now, http_status=status, post_id=post_id)
            else:
                result = _close_attempt(state, "rejected" if status in (400, 401, 403, 404, 422) else "uncertain",
                                        now, http_status=status)
            spool.save(state)
            return result
