"""Default-OFF, order-level 30 minute quote reader AB. No price policy changes.

Journal contains structured assignment/quote metadata only, never shop text.
Reservation precedes product selection; a missing journal always keeps legacy.
"""
import fcntl
import hashlib
import json
import math
import os
import re
import time
import threading
import ast

IO_TIMEOUT = 0.15
_IO_SLOTS = threading.BoundedSemaphore(8)
POLICY_PATH = os.path.join(os.path.dirname(__file__), "buyer_quote_alignment_policy.py")


def _source_environment(env):
    # Fixed local source file, literal assignments only; never execute configuration.
    try:
        with open(POLICY_PATH, encoding="utf-8") as f:
            source = f.read(4097)
        if len(source) > 4096:
            return {}
        values = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        if values.get("SOURCE_CONTROL") is not True:
            return env
        return {"BUYER_QUOTE_ALIGN": "1" if values.get("ENABLED") is True else "0",
                "BUYER_QUOTE_ALIGN_START": str(values.get("START", "")),
                "BUYER_QUOTE_ALIGN_STUDY": values.get("STUDY", "")}
    except (OSError, ValueError, SyntaxError, TypeError):
        return {}


def _bounded_io(operation, fallback):
    """Bound ALL policy/journal IO with shared, nonqueued daemon admission.

    Timeout does not release a running worker's slot; only its completion does.
    Construction/start failure leaves slot ownership with the caller.
    """
    if not _IO_SLOTS.acquire(blocking=False):
        return fallback
    slots = _IO_SLOTS
    deadline = time.monotonic() + IO_TIMEOUT
    try:
        done = threading.Event()
        result = []
        def run():
            try:
                result.append(operation())
            except Exception:
                # Local IO/parse failures preserve the baseline path.
                pass
            finally:
                slots.release()
                done.set()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
    except Exception:
        slots.release()
        return fallback
    return result[0] if done.wait(max(0., deadline - time.monotonic())) and result else fallback


def source_environment(env):
    return _bounded_io(lambda: _source_environment(env), {})


def bounded_append(path, row):
    """Timed-out writes record intent only, never quote publication."""
    return bool(_bounded_io(lambda: append(path, row), False))


def current(context, env=None, now=None):
    p = parameters(env)
    clock = time.time() if now is None else now
    return bool(context and p and context.get("study") == p[0]
                and context.get("start") == p[1] and p[1] <= clock < p[2])

import ask_read

JOURNAL = "quote_alignment.jsonl"
DURATION = 1800


def parameters(env=None):
    env = source_environment(os.environ if env is None else env)
    if env.get("BUYER_QUOTE_ALIGN", "0") != "1":
        return None
    try:
        start = float(env.get("BUYER_QUOTE_ALIGN_START", ""))
    except (ValueError, TypeError):
        return None
    study = env.get("BUYER_QUOTE_ALIGN_STUDY", "")
    if not isinstance(study, str) or not math.isfinite(start) or start <= 0 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", study):
        return None
    return study, start, start + DURATION


def append(path, row):
    """Durable single append across quote threads/processes. Fail closed on IO error."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except (OSError, ValueError, TypeError):
        return False


def reserve(md, order, now=None, env=None):
    """Assign all starting orders, including those with no eligible quote/product."""
    p = parameters(env)
    initial = time.time() if now is None else now
    oid = order.get("order")
    if not p or not isinstance(oid, str) or not oid or not p[1] <= initial < p[2]:
        return None
    arm = "AB"[hashlib.sha256((p[0] + ":" + oid).encode()).digest()[0] % 2]
    row = {"event": "assignment", "study": p[0], "order": oid, "arm": arm,
           "at": initial, "start": p[1], "end": p[2], "kind": order.get("kind"),
           "category": order.get("category")}
    path = os.path.join(md, JOURNAL)
    return dict(row, path=path) if bounded_append(path, row) and current(row, env, time.time() if now is None else now) else None


def read_quote(context, text, offer, legacy, raw, *, shop, open_, refused, late,
               receipt=False, now=None, env=None):
    """Additional read only when legacy found no number; journal both arms.

    Called outside quote_ctl's lock. Late, terminal,
    receipt and refused responses never receive a new quoted value.
    """
    p = parameters(env)
    initial = time.time() if now is None else now
    if not context or not p or context.get("study") != p[0] or context.get("start") != p[1] or not p[1] <= initial < p[2]:
        return legacy
    candidate = None
    reason = "legacy"
    if legacy is None and raw is None:
        reason = "not_open" if not open_ else "late" if late else "receipt" if receipt else "refused" if refused else None
        if reason is None:
            candidate, yours = ask_read.read(text)
            lp = offer.get("list_price")
            named = set(re.findall(r"\bG[0-9a-f]{8}\b", text or "", re.I))
            if (not isinstance(candidate, int) or isinstance(candidate, bool) or candidate <= 0 or yours
                    or not isinstance(lp, (int, float)) or isinstance(lp, bool) or not math.isfinite(lp) or lp <= 0
                    or not .05 * lp <= candidate <= lp
                    or named - {offer.get("sku")}):
                candidate, reason = None, "strict_or_bounds"
            else:
                reason = "opportunity"
    applied = candidate is not None and context.get("arm") == "B"
    row = {"event": "quote", "study": p[0], "order": context["order"], "arm": context["arm"],
           "at": initial, "shop": shop, "sku": offer.get("sku"), "legacy": legacy,
           "candidate": candidate, "reason": reason, "applied": False,
           "intended_applied": applied, "confirmation": "orders.quote_alignment_applied"}
    if not bounded_append(context["path"], row) or not current(context, env, time.time() if now is None else now):
        return legacy
    return candidate if applied else legacy
