"""B1 live listing evidence. Legacy terminal logs are never an availability source.

All disk work has bounded admission/wait. Files are prepared and fsynced before
the short publication lock; directory fsync is after unlock. An IO timeout can
leave evidence on disk, but can never authorize a treatment in its caller.
"""
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid

TTL = 300
IO_TIMEOUT = .15
MAX_BYTES = 262144
_SLOTS = threading.BoundedSemaphore(4)
ROOT = "buyer_stock_evidence"
# Source-only reviewed 30-minute experiment; END stops new assignments.
ENABLED = True
STUDY = "b1-stock-order-20261108-0210"
START = 1794071400  # 2026-11-08 02:10 KST
END = 1794073200    # 2026-11-08 02:40 KST


def _io(operation, fallback=None):
    if not _SLOTS.acquire(blocking=False):
        return fallback
    done, result = threading.Event(), []
    def run():
        try:
            result.append(operation())
        except (OSError, ValueError, TypeError, KeyError):
            pass
        finally:
            _SLOTS.release()
            done.set()
    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        _SLOTS.release()
        return fallback
    return result[0] if done.wait(IO_TIMEOUT) and result else fallback


def _number(v):
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except OverflowError:
        return False


def _bytes(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    if len(data) > MAX_BYTES:
        raise ValueError("evidence too large")
    return data


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def snapshot(shop, sku):
    """Fingerprint the complete observed SKU listing, never infer a stock revision.

    Equal public quantities do not prove equal physical stock. Any available
    revision/unit fields are retained and hashed along with all listing fields.
    Missing listing/quantity means UNKNOWN, including every legacy quote row.
    """
    if not isinstance(shop, dict) or not isinstance(shop.get("offers"), list) or len(shop["offers"]) > 512:
        return None
    offers = [o for o in shop["offers"] if isinstance(o, dict) and o.get("sku") == sku]
    if (len(offers) != 1 or not isinstance(shop.get("name"), str)
            or not isinstance(shop.get("url"), str) or not isinstance(sku, str)
            or not isinstance(offers[0].get("qty"), int) or isinstance(offers[0]["qty"], bool)
            or offers[0]["qty"] < 0):
        return None
    value = {"shop": shop["name"], "url": shop["url"], "sku": sku, "inventory": offers[0]}
    try:
        return json.loads(_bytes({**value, "fingerprint": _hash(value)}))
    except (ValueError, TypeError):
        return None


def _read(path, expected=dict):
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("evidence too large")
    value = json.loads(data)
    if not isinstance(value, expected):
        raise ValueError("invalid evidence")
    return value


def listing(path):
    """Read the caller's current local listing with the same finite IO budget."""
    return _io(lambda: _read(path, list))


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _directory(path):
    if not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent and parent != path:
            _directory(parent)
        os.makedirs(path, exist_ok=True)
        _sync_dir(parent or ".")


def _prepared(path, row):
    _directory(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(_bytes(row))
            f.flush()
            os.fsync(f.fileno())
        return tmp
    except BaseException:
        os.unlink(tmp)
        raise


def _observation_path(md, shop, sku):
    return os.path.join(md, ROOT, "availability", _hash([shop["name"], sku]) + ".json")


def observe(md, shop, sku, *, text="", ask=None, fit=False, at=None, snapshot_at=None):
    """A live response, at response time; a later numeric ask/fit clears sold-out.

    Strict whole-message refusal grammar avoids negations, hypotheticals, other
    products, and generic no-answer/no-fit/warranty refusals becoming stock proof.
    """
    at = time.time() if at is None else at
    snap = snapshot(shop, sku)
    if not snap or not _number(at) or not _number(snapshot_at if snapshot_at is not None else at):
        return False
    numeric = _number(ask) and ask > 0
    sold = bool(re.fullmatch(r"(?:Sorry,?\s*)?(?:we have none of that in stock|not in stock|"
                             r"no matching stock|out.of.stock|sold out)[.!]?", text.strip(), re.I))
    status = "AVAILABLE" if numeric or fit is True else "SOLD_OUT" if sold else "UNKNOWN"
    row = {"schema": 1, "at": at, "snapshot_at": at if snapshot_at is None else snapshot_at,
           "snapshot": snap, "status": status, "numeric_ask": numeric, "fit": fit is True}
    return bool(_io(lambda: _publish_observation(md, shop, sku, row), False))


def _publish_observation(md, shop, sku, row):
    path = _observation_path(md, shop, sku)
    tmp = _prepared(path, row)  # expensive write/fsync before publication lock
    try:
        with open(path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                prior = _read(path)
            except FileNotFoundError:
                prior = {}
            # A newer listing observation invalidates old-snapshot sold-out
            # replies even if their network response arrives after the change.
            if (prior.get("at", -1) <= row["at"] and (row["status"] != "SOLD_OUT"
                    or prior.get("snapshot_at", -1) <= row["snapshot_at"])):
                os.replace(tmp, path)
            fcntl.flock(lock, fcntl.LOCK_UN)
        _sync_dir(os.path.dirname(path))  # never fsync while owning a lock
        return True
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def availability(md, shop, sku, *, now=None):
    now = time.time() if now is None else now
    snap = snapshot(shop, sku)
    if not snap or not _number(now):
        return "UNKNOWN"
    return _io(lambda: _availability(md, shop, sku, now), "UNKNOWN")


def _availability(md, shop, sku, now):
    return _availability_info(md, shop, sku, now)["status"]


def _availability_info(md, shop, sku, now):
    unknown = {"status": "UNKNOWN", "at": None, "snapshot_at": None}
    snap = snapshot(shop, sku)
    if snap is None:
        return unknown
    try:
        row = _read(_observation_path(md, shop, sku))
    except FileNotFoundError:
        return unknown
    if row.get("snapshot") != snap:
        # Persist an invalidation, so a later return to an earlier public qty
        # cannot revive its obsolete SOLD_OUT evidence.
        _publish_observation(md, shop, sku, {"schema": 1, "at": now, "snapshot_at": now,
                             "snapshot": snap, "status": "UNKNOWN", "reason": "listing_changed"})
        return unknown
    if (row.get("schema") != 1
            or not _number(row.get("at")) or not 0 <= now - row["at"] < TTL
            or not _number(row.get("snapshot_at")) or not 0 <= now - row["snapshot_at"] < TTL):
        return unknown
    return {"status": row.get("status") if row.get("status") in ("SOLD_OUT", "AVAILABLE") else "UNKNOWN",
            "at": row["at"], "snapshot_at": row["snapshot_at"]}


def plan(md, baseline, fits, rivals, selected, *, k, now=None):
    """Same-input first-prefetch comparison. Preserve every partition's slots.

    k-1 prefetch slots are the live exposure before the choice; the chooser's
    candidate list, review ordering and later quote count remain untouched.
    A concurrent batch with the same membership is not an opportunity.
    Oversized or unreadable evidence leaves the entire baseline unchanged.
    """
    now = time.time() if now is None else now
    if len(baseline) > 256:
        return None  # preserve the whole runtime baseline, never a truncated plan
    def build():
        inputs, groups = [], {}
        for i, s in enumerate(baseline):
            n, sku = s["name"], selected[s["name"]]
            observation = _availability_info(md, s, sku, now)
            inputs.append({"shop": n, "sku": sku, "snapshot": snapshot(s, sku),
                           "listing_fingerprint": _hash(s.get("offers")),
                           "fit_count": fits[n], "reviews": len(s.get("reviews") or []),
                           "rival": n in rivals, "availability": observation["status"], "observation": observation})
            groups.setdefault((fits[n] > 0, n in rivals), []).append(i)
        names = [s["name"] for s in baseline]
        treatment = list(names)
        for indices in groups.values():
            moved = sorted(indices, key=lambda i: inputs[i]["availability"] == "SOLD_OUT")
            for dst, src in zip(indices, moved):
                treatment[dst] = names[src]
        first_a, first_b = names[:max(0, k - 1)], treatment[:max(0, k - 1)]
        result = {"schema": 1, "at": now, "baseline": names, "treatment": treatment,
                  "first_baseline": first_a, "first_treatment": first_b,
                  "eligible": set(first_a) != set(first_b), "inputs": inputs, "k": k,
                  "valid_until": min((min(i["observation"]["at"], i["observation"]["snapshot_at"]) + TTL
                                      for i in inputs if i["availability"] == "SOLD_OUT"), default=None)}
        _bytes(result)  # full input or UNKNOWN; never truncate candidates
        return result
    return _io(build)


def _uuid(oid):
    try:
        return str(uuid.UUID(oid)) if isinstance(oid, str) and len(oid) == 36 else None
    except ValueError:
        return None


def _order_id(oid):
    # Genuine kit orders use o- plus twelve hex digits. Preserve their full key;
    # missing historical UUID suffixes cannot be reconstructed.
    return _uuid(oid) or (oid if isinstance(oid, str) and re.fullmatch(r"o-[0-9a-f]{12}", oid) else None)


def _active(now):
    return (ENABLED is True and isinstance(STUDY, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", STUDY))
            and _number(START) and _number(END) and 0 < START <= now < END and END - START <= 1800)


def _order_dir(md, oid, study):
    return os.path.join(md, ROOT, "orders", _hash([study, oid]))


def _once(path, row):
    """Atomic immutable publication, no application lock or fsync under a lock.

    link is exclusive across processes. A concurrent writer sees a complete,
    fsynced file, never an empty O_EXCL placeholder. Sync the directory before
    reporting durable success even when a prior/timeout writer won the link.
    """
    tmp = _prepared(path, row)
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        _sync_dir(os.path.dirname(path))
        return _read(path)
    finally:
        os.unlink(tmp)


def reserve(md, order, *, now=None):
    """Reserve ALL starting goods orders, including no shops/no opportunity/failure.

    SHA256 hashes a full canonical allocation UUID and study, never a UUID
    prefix or alternating journal length. Actual kit o-12hex keys receive a
    deterministic UUID5 allocation; that is not a recovered historical UUID.
    Finite samples need not have equal arm counts. No attempt is inferred here.
    """
    live_clock = now is None
    now = time.time() if live_clock else now
    oid = _order_id(order.get("order"))
    started = order.get("_buyer_started_at")
    if (not _active(now) or not oid or order.get("kind") != "goods"
            or not _number(started) or not START <= started <= now):
        return None
    study, start, end = STUDY, START, END
    allocation_id = _uuid(oid) or str(uuid.uuid5(uuid.NAMESPACE_URL, study + ":" + oid))
    digest = hashlib.sha256((study + ":" + allocation_id).encode()).digest()
    row = {"schema": 1, "event": "assignment", "order": oid, "study": study,
           "allocation_id": allocation_id,
           "arm": "AB"[int.from_bytes(digest, "big") % 2], "at": now,
           "started_at": started, "start": start, "end": end, "kind": "goods"}
    directory = _order_dir(md, oid, study)
    def write():
        prior = _once(os.path.join(directory, "assignment.json"), row)
        if any(prior.get(k) != row[k] for k in ("schema", "event", "order", "study", "allocation_id", "arm", "start", "end", "kind")):
            return None
        return {**prior, "directory": directory}
    result = _io(write)
    clock = time.time() if live_clock else now
    return result if _active(clock) and (STUDY, START, END) == (study, start, end) else None


def decide(context, decision, *, current=None, now=None):
    """Durable decision intent before scheduling; never an actual-attempt witness.

    Ineligible and unknown orders remain in ITT. Any pre-dispatch IO failure or
    expired/OFF/conflicting configuration returns the same baseline candidates.
    """
    if not decision:
        return None
    baseline = decision["baseline"]
    clock = time.time() if now is None else now
    if not context or not _active(clock) or (context["study"], context["start"], context["end"]) != (STUDY, START, END):
        return baseline
    def validate():
        at = time.time() if now is None else now
        try:
            fresh = _io(lambda: current(at)) if callable(current) else None
        except (OSError, ValueError, TypeError, KeyError):
            fresh = None
        # Reading the latest evidence itself takes IO: test its actual evidence
        # expiry again at completion, not merely the age of the plan object.
        at = time.time() if now is None else now
        valid = (fresh is not None and _plan_signature(fresh) == _plan_signature(decision)
                 and _active(at) and (context["study"], context["start"], context["end"]) == (STUDY, START, END)
                 and fresh["eligible"] and _number(fresh.get("valid_until")) and at < fresh["valid_until"])
        return fresh, valid
    fresh, valid = validate()
    intended = context["arm"] == "B" and valid
    row = {"schema": 1, "event": "decision", "order": context["order"], "study": context["study"],
           "arm": context["arm"], "plan": fresh, "proposed_reorder": intended,
           "intended_reorder": None if intended else False,
           "actual_attempt": None, "at": clock}
    def write():
        prior = _once(os.path.join(context["directory"], "decision.json"), row)
        return (all(prior.get(k) == row[k] for k in ("schema", "event", "order", "study", "arm", "proposed_reorder"))
                and _plan_signature(prior.get("plan") or {}) == _plan_signature(fresh or {}))
    persisted = _io(write, False)
    # A durable proposal can outlive its evidence or experiment. It is never a
    # confirmed dispatch. Re-read all live inputs after journal IO, then return.
    _, valid = validate() if persisted and intended else (None, False)
    return decision["treatment"] if persisted and intended and valid else baseline


def _plan_signature(decision):
    return {k: v for k, v in decision.items() if k != "at"}


def attempt(context, shop, sku, *, phase, intended_reorder=None, at=None):
    """Witness only AFTER Thread.start succeeds. This does not claim RPC or payment."""
    if not context:
        return False
    uid = str(uuid.uuid4())
    row = {"schema": 1, "event": "attempt", "id": uid, "order": context["order"],
           "study": context["study"], "arm": context["arm"], "shop": shop, "sku": sku,
           "phase": phase, "intended_reorder": intended_reorder,
           "boundary": "quote_thread_started", "at": time.time() if at is None else at}
    return bool(_io(lambda: _once(os.path.join(context["directory"], "attempt-" + uid + ".json"), row), False))


def terminal(context, *, state, passed_to_kit, at=None):
    """Record terminal independently of reservation/attempt. Hand-off is not payment."""
    if not context:
        return False
    row = {"schema": 1, "event": "terminal", "order": context["order"], "study": context["study"],
           "arm": context["arm"], "state": state, "passed_to_kit": passed_to_kit is True,
           "at": time.time() if at is None else at}
    return bool(_io(lambda: _once(os.path.join(context["directory"], "terminal.json"), row), False))


def measurement(order):
    ctx = order.get("_stock_evidence") or {}
    return {"arm": ctx.get("arm"), "study": ctx.get("study"), "reserved": bool(ctx),
            "reservation_status": order.get("_stock_reservation_status", "off"),
            "eligible": (order.get("_stock_plan") or {}).get("eligible"),
            "intended_reorder": order.get("_stock_reordered", False) if order.get("_stock_dispatched") else None,
            "witness_failures": order.get("_stock_witness_failures", 0),
            "terminal_written": order.get("_stock_terminal_written")}


def read_order(md, oid, study):
    """Bounded public audit seam. Missing events remain null, never zero effects."""
    oid = _order_id(oid)
    if not oid:
        return None
    directory = _order_dir(md, oid, study)
    def read():
        result = {"assignment": None, "decision": None, "attempts": [], "terminal": None}
        try:
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries):
                    if count >= 128:
                        raise ValueError("too many order events")
                    if not entry.name.endswith(".json"):
                        continue
                    row = _read(entry.path)
                    event = row.get("event")
                    if event == "attempt":
                        result["attempts"].append(row)
                    elif event in ("assignment", "decision", "terminal"):
                        result[event] = row
            result["attempts"].sort(key=lambda r: (r["at"], r["id"]))
        except FileNotFoundError:
            pass
        return result
    return _io(read)
