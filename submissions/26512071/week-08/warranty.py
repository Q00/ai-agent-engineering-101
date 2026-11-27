"""warranty-72h-v1: bounded 72 h warranty on goods grade A/B, reserved at full sell price. Local files only, no network.

Lifecycle, all under $MARKET_DIR/restock-plan.lock (the lock restock_r3/oneday_pilot already use):
  init    -> the parent creates the signed genesis state explicitly, BEFORE use; never re-initialised. Without it no
             72 h is quoted or issued (restock treats a missing pair as 0 liability: the documented rollback limit).
  quote   -> an eligible first quote reserves an INTENT at the opening ask (an upper bound: asks never rise) only if the
             source gate binds all restock_r3.SOURCES (16, incl. this file), the one-day pilot is active, and a FRESH
             oneday_pilot.context over inventory.json + restock-pending.json + the frozen risk ledger, evaluated under
             the lock, leaves cash/bank/gross-loss room > 0 after it. one-day-pilot-status.json is a report, never an
             authority (a future-dated one is an anomaly: fail closed). Else the quote says 0 h.
  issue   -> before the receipt is signed the intent becomes an AUTHORITY bound to the exact receipt core
             (shop/item/price/task digest), terms and gate source hashes (persisted first; sign only after). The fresh
             context is re-evaluated: a balance drop or pending committed after the quote refuses. Any failure (no
             intent, unit/grade change, pilot inactive, headroom, price above the reserve, corrupt state, lock busy)
             -> the seller refuses unsigned; a promised 72 h is never silently reduced to 0 h.
  release -> only an unsigned intent is freed (cancel/stale/reject/unavailable/startup orphan). An issued authority
             is kept FOREVER at its full reserve: the old shared kit's JWTs carry no exp, the warranty starts at the
             actual settlement, and no exact terminal mapping exists yet, so reconcile() releases nothing (no TTL,
             no issued+72h, no same-unit settlement join; several receipts may name one unit).
Integrity: the whole state is signed with the shop key (canonical JSON); a separately signed head (seq, sha) rejects
a rollback to an older signed state, a tampered state or a deleted state. Limitation: restoring BOTH files to an
older pair is not detectable locally. Any invalid state fails closed (no new 72 h, restock blocked); never reset.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization

import oneday_pilot
from evidence_memory import atomic_json, locked

POLICY = "warranty-72h-v1"
HOURS = 72
STATE = "warranty-ledger.json"
HEAD = "warranty-head.json"
LOCK = "restock-plan.lock"
SKEW = 300   # seconds: a status file dated further in the future than this is an anomaly (same as oneday_pilot.SKEW_S)
OK_REASONS = ("active", "capacity_full", "lifetime_cap")   # acquisition-only blockers; money checks still apply
KEY = Path("shop_key.pem")
BOOT = f"{os.getpid()}-{os.urandom(4).hex()}"


class Corrupt(Exception):
    pass


class Uninitialized(Corrupt):
    """Neither state nor head exists: init() was never run (or both were removed: whole-state rollback limit)."""


def eligible(unit):
    return isinstance(unit, dict) and unit.get("kind") == "goods" and unit.get("grade") in ("A", "B")


def _canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _key(key):
    return serialization.load_pem_private_key(Path(key or KEY).read_bytes(), password=None)


def _signed(body, key):
    return dict(body, sig=base64.b64encode(_key(key).sign(_canon(body))).decode())


def _verify(doc, key):
    if not isinstance(doc, dict) or not isinstance(doc.get("sig"), str):
        raise Corrupt("unsigned")
    body = {k: v for k, v in doc.items() if k != "sig"}
    try:
        _key(key).public_key().verify(base64.b64decode(doc["sig"]), _canon(body))
    except Exception as e:
        raise Corrupt(f"signature:{type(e).__name__}")
    return body


def _int(v):
    return type(v) is int and v > 0


def _load(state, key):
    state = Path(state)
    sp, hp = state / STATE, state / HEAD
    if not sp.exists():
        if hp.exists():
            raise Corrupt("state_missing_head_present")
        raise Uninitialized("warranty state not initialised")
    try:
        doc = json.loads(sp.read_bytes())
        head = json.loads(hp.read_bytes()) if hp.exists() else None
    except (OSError, ValueError) as e:
        raise Corrupt(f"unreadable:{type(e).__name__}")
    body = _verify(doc, key)
    if head is None:
        raise Corrupt("head_missing")
    head = _verify(head, key)
    sha = hashlib.sha256(_canon(body)).hexdigest()
    # head.seq == seq: normal. seq == head.seq+1 chained to head.sha: crash between the two writes (forward only).
    if not ((head.get("seq") == body.get("seq") and head.get("sha") == sha) or
            (body.get("seq") == head.get("seq", -2) + 1 and body.get("prev") == head.get("sha"))):
        raise Corrupt("head_mismatch")
    if body.get("policy") != POLICY or not isinstance(body.get("intents"), dict) \
            or not isinstance(body.get("authorities"), dict):
        raise Corrupt("schema")
    for rows in (body["intents"], body["authorities"]):
        for r in rows.values():
            if not isinstance(r, dict) or not _int(r.get("reserve")) or r.get("hours") != HOURS:
                raise Corrupt("row")
    if set(body["intents"]) & set(body["authorities"]):
        raise Corrupt("intent_and_authority")
    return body


def _save(state, body, key, genesis=False):
    state = Path(state)
    prev = None if genesis else hashlib.sha256(_canon(_load(state, key))).hexdigest()
    body = dict(body, seq=body["seq"] + 1, prev=prev)
    atomic_json(state / STATE, _signed(body, key))
    atomic_json(state / HEAD, _signed({"seq": body["seq"], "sha": hashlib.sha256(_canon(body)).hexdigest()}, key))


def init(state, key=None, timeout=5):
    """Parent-only, once, BEFORE the seller may quote 72 h: write the signed empty genesis state and head.
    Refuses if either file exists (never a reset)."""
    state = Path(state)
    with locked(state / LOCK, timeout):
        if (state / STATE).exists() or (state / HEAD).exists():
            raise ValueError("warranty state already initialised: never reset")
        _save(state, {"policy": POLICY, "seq": 0, "intents": {}, "authorities": {}}, key, genesis=True)


def _total(body):
    return sum(r["reserve"] for r in body["intents"].values()) + sum(r["reserve"] for r in body["authorities"].values())


def liability(state, key=None):
    """Intents + issued authorities at full reserve. Raises Corrupt on any invalid state."""
    return _total(_load(state, key))


def pilot_liability(state, key=None):
    """For oneday_pilot.context (no lock taken here; the caller holds restock-plan.lock): the liability, 0 when never
    initialised (no 72 h can have been issued without init), or None when the state is invalid (fail closed)."""
    try:
        return liability(state, key)
    except Uninitialized:
        # Once the parent gate carries the leader_warranty deployment marker, a missing pair means deleted state
        # (authorities may exist): fail closed. Unmarked/legacy states were never initialised: 0.
        gate = oneday_pilot._gate(state)
        return None if isinstance(gate, dict) and gate.get("leader_warranty") is not None else 0
    except (Corrupt, OSError, ValueError, TypeError, KeyError):
        return None


def _num(v):
    return type(v) in (int, float) and math.isfinite(v)


def _pilot_active(state, now):
    """The approved, gate-bound, unexpired one-day pilot config, else None. New 72 h only while this holds."""
    state = Path(state)
    if not _num(now) or oneday_pilot.preflight(state) is not None:
        return None
    gate = oneday_pilot._gate(state)
    if not isinstance(gate, dict) or gate.get("approved") is not True or gate.get("policy_version") != "bounded-v2":
        return None
    config = json.loads((state / oneday_pilot.CONFIG).read_bytes())
    if not config["activated_at"] <= now < config["expires_at"]:
        return None
    return config


def _sources(state):
    """The gate's reviewed source hashes when restock_r3.approved binds every SOURCES file (16, incl. this module),
    else None. Lazy import: restock_r3 imports oneday_pilot, which imports this module lazily."""
    import restock_r3
    if not restock_r3.approved(state):
        return None
    hashes = oneday_pilot._gate(state)["source_sha256"]
    return {n: hashes[n] for n in restock_r3.SOURCES}


def _money(state, now):
    """Room left for warranty reserve, net of ALL current liability, or None. Caller holds restock-plan.lock.
    Fresh: inventory.json (balance, units) + restock-pending.json + frozen risk ledger + one-day ledger through
    oneday_pilot.context, whose cash/bank room already subtract warranty.pilot_liability once and whose gross_loss
    excludes it (so it is subtracted here exactly once). The status file is never an authority."""
    import risk_pilot
    state = Path(state)
    st = state / oneday_pilot.STATUS
    if st.exists():
        u = json.loads(st.read_text(encoding="utf-8")).get("updated_at")
        if not _num(u) or u > now + SKEW:
            return None   # forged/future-dated report: anomaly, fail closed
    inv = json.loads((state / "inventory.json").read_text(encoding="utf-8"))
    if not isinstance(inv, dict) or inv.get("round", 0) < 3:
        return None
    pj = state / "restock-pending.json"
    pending = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else []
    if not isinstance(pending, list) or not all(isinstance(r, dict) for r in pending):
        return None
    led = risk_pilot.load_ledger(state)
    ids, complete = risk_pilot.frozen(pending, led)
    ctx = oneday_pilot.context(state, now, inv, pending, ids, complete, led)
    if ctx.get("reason") not in OK_REASONS:
        return None
    vals = (ctx.get("cash_room"), ctx.get("bank_room"), ctx.get("gross_loss"), ctx.get("warranty_liability"))
    if not all(_num(v) for v in vals):
        return None
    cash, bank, loss, wl = vals
    if ctx.get("financial_limits_mode") == "disabled":   # amendment #8: balance - in-flight reserves only
        avail = (ctx.get("financial_limits") or {}).get("available_cash")
        return avail if _num(avail) else None
    if ctx.get("financial_limits_mode") != "enforced":
        return None
    mode = ctx.get("loss_ceiling_mode")
    if mode == "disabled":   # amendment #7: no combined loss+warranty ceiling; cash/bank room already net of wl
        return min(cash, bank)
    if mode != "enforced":
        return None
    return min(cash, bank, ctx["caps"]["gross_loss"] - loss - wl)


def quote(state, task, unit, ask, now, key=None, timeout=1.5):
    """72 if an intent at `ask` (the opening ask, >= any later price) was reserved for this task, else 0. Never raises.
    The room must stay > 0 after the intent (context treats cash <= 0 as insufficient)."""
    if not eligible(unit) or not _int(ask) or not _num(now):
        return 0
    try:
        with locked(Path(state) / LOCK, timeout):
            body = _load(state, key)
            if task in body["authorities"]:
                return 0
            if _pilot_active(state, now) is None or _sources(state) is None:
                return 0
            room = _money(state, now)
            if room is None or ask - body["intents"].get(task, {}).get("reserve", 0) >= room:
                return 0
            body["intents"][task] = {"unit": unit.get("unit"), "sku": unit.get("sku"), "reserve": ask, "hours": HOURS,
                                     "quoted_at": now, "boot": BOOT}
            _save(state, body, key)
            return HOURS
    except Exception:   # corrupt/uninitialised state, lock busy, unreadable pilot files: honest 0 h quote
        return 0


def receipt_core(shop, unit, price, task):
    """Exactly what seller_r3.sign covers: shop, item, price, task."""
    return {"shop": shop, "item": unit.get("sku"), "price": price, "task": task}


def issue(state, task, unit, price, now, key=None, timeout=3, shop=None):
    """Persist the authority BEFORE the receipt is signed. 'issued' or the refusal reason. Never raises.
    The authority binds the exact receipt core digest, the terms, the policy and the gate's source hashes."""
    try:
        with locked(Path(state) / LOCK, timeout):
            body = _load(state, key)
            core = receipt_core(shop, unit, price, task)
            digest = hashlib.sha256(_canon(core)).hexdigest()
            if task in body["authorities"]:
                a = body["authorities"][task]
                same = (a.get("unit"), a.get("sku"), a.get("price"), a.get("receipt_core_sha256")) == \
                    (unit.get("unit"), unit.get("sku"), price, digest)
                return "issued" if same else "conflict"
            intent = body["intents"].get(task)
            if intent is None:
                return "no_intent"
            if intent.get("unit") != unit.get("unit") or intent.get("sku") != unit.get("sku") or not eligible(unit):
                return "unit_mismatch"   # incl. a grade drop to C: never a silent 0 h receipt
            if type(price) is not int or price <= 0 or price > intent["reserve"]:
                return "price_above_reserve"
            if _pilot_active(state, now) is None:
                return "pilot_inactive"
            sources = _sources(state)
            if sources is None:
                return "source_gate"
            room = _money(state, now)   # the intent is already inside the liability: room must still be >= 0
            if room is None or room < 0:
                return "headroom"
            del body["intents"][task]
            body["authorities"][task] = dict(
                intent, price=price, issued_at=now, status="issued_unknown", policy=POLICY,
                receipt_core=core, receipt_core_sha256=digest,
                terms={"cert": True, "warranty_hours": HOURS, "claim": unit["grade"]}, source_sha256=sources,
                note="signed_unknown: kept at full reserve until an exact terminal mapping exists")
            _save(state, body, key)
            return "issued"
    except Corrupt:
        return "corrupt"
    except TimeoutError:
        return "lock_busy"
    except Exception as e:
        return f"error:{type(e).__name__}"


def release(state, task, why, now, key=None, timeout=3):
    """Free an unsigned intent only. 'released', 'issued_kept' (never freed) or 'absent'. Raises on invalid state."""
    with locked(Path(state) / LOCK, timeout):
        body = _load(state, key)
        if task in body["authorities"]:
            return "issued_kept"
        if task not in body["intents"]:
            return "absent"
        del body["intents"][task]
        _save(state, body, key)
        return "released"


def startup(state, now, key=None, timeout=5):
    """Seller boot: restore and validate; free intents of earlier boots (never issued: issue() needs the intent under
    the lock, so a released intent can never be signed later). Raises Corrupt on invalid state."""
    with locked(Path(state) / LOCK, timeout):
        body = _load(state, key)
        orphans = [t for t, r in body["intents"].items() if r.get("boot") != BOOT]
        for t in orphans:
            del body["intents"][t]
        if orphans:
            _save(state, body, key)
        return {"intents": len(body["intents"]), "authorities": len(body["authorities"]), "released": len(orphans),
                "liability": _total(body)}


def reconcile(state, now, settled_units=None, key=None):
    """Initial safe reconciler: releases NOTHING. Same-unit settlement is not an exact join (many receipts may name one
    unit) and refunds are not yet mapped to authorities, so every issued authority stays at full reserve."""
    body = _load(state, key)
    return {"retained": len(body["authorities"]), "released": 0, "reason": "no exact terminal mapping (retain all)"}
