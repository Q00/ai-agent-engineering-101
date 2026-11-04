# C8 live observation (L2, CR REVIEW_M2r2_L B1). Builds the obs dict approval.watch needs after an apply:
#   {source: "live", point, lo, hi, n, n_after_h, n_before_h, loss, base_balance, concurrent, t_last}
# point = mean hourly gross profit (sum amount - cost of OUR settles) in the complete hours since the apply
#         minus the mean hourly gross profit in the BASELINE_S before the apply (KRW per hour).
# lo/hi = point -/+ Z * Welch standard error (needs >= 2 hours on both sides, else None).
# loss  = max(0, -point) * hours since the apply (KRW), compared with base_balance by prereg.over_cap.
# Fail-closed: no rows, a stale source (last row older than C.STALE_S before t_now) or bad rows -> (None, reason);
# approval.watch_cycle then turns RLPO OFF (no silent watch). C.LIVE_OBS_READY stays False until this module passes CR.
import math

from rlpo import config as C

BASELINE_S = 24 * 3600
Z = 1.96
HOUR = 3600


def _gp(s):
    a, c = s.get("amount"), s.get("cost")
    if isinstance(a, bool) or isinstance(c, bool) or not isinstance(a, (int, float)) or not isinstance(c, (int, float)):
        raise ValueError(f"bad sale row (amount/cost): {s!r}"[:200])
    return float(a) - float(c)


def _hours(sales, t0, n):
    # n complete hour blocks starting at t0 -> list of gross-profit sums
    out = [0.0] * n
    for s in sales:
        k = int((s["ts"] - t0) // HOUR)
        if 0 <= k < n:
            out[k] += _gp(s)
    return out


def _mv(x):
    m = sum(x) / len(x)
    v = sum((y - m) ** 2 for y in x) / (len(x) - 1) if len(x) > 1 else None
    return m, v


def observe(sales, t_apply, t_now, base_balance, concurrent=(), baseline_s=BASELINE_S):
    if not sales:
        return None, "C8: no live sale rows"
    try:
        t_last = max(float(s["ts"]) for s in sales)
    except (KeyError, TypeError, ValueError) as e:
        return None, f"C8: bad sale rows ({type(e).__name__})"
    if t_now - t_last > C.STALE_S:
        return None, f"C8: live source stale (last row {t_now - t_last:.0f} s before t_now > {C.STALE_S})"
    n_after = int((t_now - t_apply) // HOUR)
    n_before = int(baseline_s // HOUR)
    if n_after < 1:
        return None, "C8: no complete hour since the apply yet"
    try:
        after = _hours(sales, t_apply, n_after)
        before = _hours(sales, t_apply - n_before * HOUR, n_before)
    except (KeyError, TypeError, ValueError) as e:
        return None, f"C8: {e}"[:300]
    ma, va = _mv(after)
    mb, vb = _mv(before)
    point = ma - mb
    lo = hi = None
    if va is not None and vb is not None:
        se = math.sqrt(va / len(after) + vb / len(before))
        lo, hi = point - Z * se, point + Z * se
    n = sum(1 for s in sales if t_apply <= s["ts"] < t_apply + n_after * HOUR)
    return {"source": "live", "point": point, "lo": lo, "hi": hi, "n": n, "n_after_h": n_after,
            "n_before_h": n_before, "loss": max(0.0, -point) * n_after, "base_balance": base_balance,
            "concurrent": tuple(concurrent), "t_last": t_last}, None


def recent(sales, t_now, window_s=BASELINE_S):
    # structured live performance for the approver prompt (numbers only); None when the source is stale or empty
    if not sales:
        return None
    try:
        t_last = max(float(s["ts"]) for s in sales)
        if t_now - t_last > C.STALE_S:
            return None
        n_h = int(window_s // HOUR)
        h = _hours(sales, t_now - n_h * HOUR, n_h)
    except (KeyError, TypeError, ValueError):
        return None
    m, v = _mv(h)
    return {"gp_per_h_24h": m, "gp_sd_h_24h": math.sqrt(v) if v is not None else None,
            "sales_24h": sum(1 for s in sales if t_now - n_h * HOUR <= s["ts"] < t_now),
            "last_row_age_s": t_now - t_last}
