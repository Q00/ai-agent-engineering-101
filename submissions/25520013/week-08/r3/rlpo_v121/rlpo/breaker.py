# M2r LLM breaker + token ledger (S2). Persisted inside the RLPO state (rstate) so it survives restarts.
# Rules mirror the B r3d auto-OFF as relayed (EL summary): quota / 429 -> OFF 1 h; auth -> OFF until next KST
# midnight; model error -> OFF latch (human); timeout / 5xx / other -> OFF 1 h after 3 in a row.
# [limit] v120c/B/ is off-limits for this task, so the B r3d module is mirrored here, not imported; the rules
# must be diffed against B r3d before wiring (open item).
import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))
HOUR = 3600


def classify(err):
    m = f"{type(err).__name__}: {err}".lower()
    if "auth" in m or "401" in m or "403" in m:
        return "auth"
    if "quota" in m or "429" in m or "rate limit" in m:
        return "quota"
    if "model" in m and ("not found" in m or "unknown" in m or "unsupported" in m or "missing" in m):
        return "model"
    if "timeout" in m or "timed out" in m:
        return "timeout"
    if any(c in m for c in (" 500", " 502", " 503", " 504", "5xx")):
        return "5xx"
    return "other"


def next_kst_midnight(t):
    d = dt.datetime.fromtimestamp(t, KST).date() + dt.timedelta(days=1)
    return dt.datetime(d.year, d.month, d.day, tzinfo=KST).timestamp()


def allow(b, t):
    # b = state["breaker"] dict -> (ok, reason)
    if b.get("latched"):
        return False, f"breaker latched: {b.get('why')}"
    if b.get("off_until") and t < b["off_until"]:
        return False, f"breaker off until {b['off_until']} ({b.get('why')})"
    return True, None


def record_ok(b, t, tokens_in, tokens_out):
    b["consec"] = 0
    day = dt.datetime.fromtimestamp(t, KST).strftime("%Y-%m-%d")
    led = b.setdefault("tokens", {}).setdefault(day, {"calls": 0, "in": 0, "out": 0})
    led["calls"] += 1
    led["in"] += int(tokens_in or 0)
    led["out"] += int(tokens_out or 0)
    return b


def _ledger(b, t):
    day = dt.datetime.fromtimestamp(t, KST).strftime("%Y-%m-%d")
    return day, b.setdefault("tokens", {}).setdefault(day, {"calls": 0, "in": 0, "out": 0})


def day_report(b, t, warn_tokens):
    # P1 (CoS 16:27, 성수님 14:38 무제한 결정): daily LLM call/token REPORT + warning only. No hard cap, no OFF latch.
    day, led = _ledger(b, t)
    tok = led["in"] + led["out"]
    return {"day_kst": day, "calls": led["calls"], "errors": led.get("errors", 0), "tokens_in": led["in"],
            "tokens_out": led["out"], "tokens": tok, "warn_tokens": warn_tokens,
            "warn": warn_tokens is not None and tok >= warn_tokens, "warned": bool(led.get("warned"))}


def mark_warned(b, t):
    _ledger(b, t)[1]["warned"] = True


def record_error(b, t, err):
    k = classify(err)
    _ledger(b, t)[1]["errors"] = _ledger(b, t)[1].get("errors", 0) + 1
    b["last_error"] = {"t": t, "kind": k, "msg": str(err)[:200]}
    if k == "quota":
        b.update(off_until=t + HOUR, why=k)
    elif k == "auth":
        b.update(off_until=next_kst_midnight(t), why=k)
    elif k == "model":
        b.update(latched=True, why=k)
    else:
        b["consec"] = b.get("consec", 0) + 1
        if b["consec"] >= 3:
            b.update(off_until=t + HOUR, why=f"{k} x3", consec=0)
    return k
