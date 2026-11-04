# M2r replay adapter (CR cr_rlpo_M2 C5, C7, X4).
# stats(deltas, t0, t1, base_h) -> {point, lo, hi, n, n_blocks, lo_t, lo_boot, lo_adj, alpha, money_frac}
#   point/lo/hi: legacy 95 % hour-block bootstrap (v120c replay.per_hour, one seed) - reported only.
#   lo_adj = min(lo_t, lo_boot): one-sided lower bound at C.ALPHA_ADJ (Bonferroni over C.CYCLES_PER_DAY cycles):
#     lo_t    Student-t on the hour-block sums (df = n_blocks - 1)
#     lo_boot hour-block bootstrap quantile, C.N_BOOT_JUDGE draws (numpy, seed C.SEED)
#   The 20-seed rule is gone (C7): lo = min over seeds made seed_pos == 20 whenever lo > 0 (no-op).
# CausalAdapter(probe, t_d): judges a price_mult proposal at decision time t_d on sales in [t_d - lookback, t_d)
#   with a buyer-survival function built only from market rows before t_d (no look-ahead, C5).
#   accept_prob has NO 1.0 fallback: a raised sale without price history, or whose paid ratio r0 is above every
#   observed ratio (S(r1)/S(r0) == 1 for any raise), carries no evidence and is left out of n (C5 counterexample).
#   A price cut is "unjudgeable" (extra demand is not in the records) -> n = 0, never a win.
# Adapter(probe): legacy M2 full-window adapter (look-ahead); causal = False, so guard.wins never accepts it.
# Every result: verdict (ok | unsupported | unjudgeable_cut | no_evidence), causal, keys, unsupported,
#   apply_blocked (a key in C.APPLY_BLOCKED_KEYS; price_mult is blocked on every path).
# Read-only on v120c; no network.
import math
import random

import numpy as np

from rlpo import config as C


# ---------------- Student t quantile (no scipy on the box) -----------------------------------------------------
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c if abs(c) > 1e-300 else 1e300
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-14:
            break
    return h


def _ibeta(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1 - x) / b


def t_cdf(t, df):
    x = df / (df + t * t)
    p = 0.5 * _ibeta(df / 2.0, 0.5, x)
    return 1.0 - p if t > 0 else p


def t_ppf(q, df):
    # quantile of Student t with df degrees of freedom, q in (0.5, 1)
    lo, hi = 0.0, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------- judge statistics -----------------------------------------------------------------------------
def blocks_of(deltas, t0, t1, data_ts=None):
    # hour blocks of [t0, t1). P2 (CR REVIEW_M2r): with data_ts, only hours that contain at least one recorded row
    # count as blocks (a 24 h window with 13.7 h of data has ~14 blocks, not 24). Without data_ts: window hours.
    nb = max(1, int(math.ceil((t1 - t0) / 3600 - 1e-9)))
    b = [0.0] * nb
    for x in deltas:
        ts, d = x[0], x[1]
        if t0 <= ts < t1:
            b[min(nb - 1, int((ts - t0) // 3600))] += d
    if data_ts is None:
        return b
    keep = sorted({min(nb - 1, int((ts - t0) // 3600)) for ts in data_ts if t0 <= ts < t1})
    return [b[i] for i in keep]


def data_hours(data_ts, t0, t1):
    return len(blocks_of([], t0, t1, data_ts))


def lower_bounds(blocks, hours, alpha=None, n_boot=None, seed=None):
    alpha = C.ALPHA_ADJ if alpha is None else alpha
    n_boot = C.N_BOOT_JUDGE if n_boot is None else n_boot
    nb = len(blocks)
    if nb < 2 or hours <= 0:
        return None, None
    a = np.asarray(blocks, dtype=float)
    m, sd = a.mean(), a.std(ddof=1)
    lo_t = (m - t_ppf(1 - alpha, nb - 1) * sd / math.sqrt(nb)) * nb / hours
    rng = np.random.default_rng(C.SEED if seed is None else seed)
    boots = np.sort(a[rng.integers(0, nb, size=(n_boot, nb))].sum(axis=1) / hours)
    lo_b = float(boots[int(math.floor(alpha * n_boot))])
    return float(lo_t), lo_b


def stats(deltas, t0, t1, base_h, per_hour=None, alpha=None, n_boot=None, data_ts=None):
    hours = (t1 - t0) / 3600
    out = {"point": None, "lo": None, "hi": None, "n": len(deltas), "n_blocks": 0, "lo_t": None,
           "lo_boot": None, "lo_adj": None, "alpha": C.ALPHA_ADJ if alpha is None else alpha, "money_frac": None,
           "hours": hours}
    if not deltas:
        out["n"] = 0
        return out
    if per_hour is None:
        import replay as RP
        per_hour = RP.per_hour
    pt, lo, hi, _ = per_hour(deltas, t0, t1, C.SEED)
    bl = blocks_of(deltas, t0, t1, data_ts)
    hj = hours if data_ts is None else len(bl)          # P2: per data hour when data_ts is given
    lt, lb = lower_bounds(bl, hj, alpha, n_boot)
    if data_ts is None:
        mf = (pt / base_h) if base_h else None
    else:
        mf = (sum(x[1] for x in deltas if t0 <= x[0] < t1) / hj / base_h) if (base_h and hj) else None
    out.update(point=pt, lo=lo, hi=hi, n_blocks=len(bl), lo_t=lt, lo_boot=lb,
               lo_adj=(min(lt, lb) if lt is not None and lb is not None else None), money_frac=mf,
               data_hours=None if data_ts is None else hj)
    return out


# ---------------- adapters -------------------------------------------------------------------------------------
def diff_keys(base, cand):
    import probe as PR
    fb, fc = PR._flat(base), PR._flat(cand)
    return sorted(k for k in set(fb) | set(fc) if fb.get(k) != fc.get(k))


def unsupported_of(diff):
    return [k for k in diff if k.split(".")[-1] not in C.REPLAY_KEYS or k.startswith("buyer.")]


def _finish(r, diff, un, verdict, causal):
    keys = sorted({k.split(".")[-1] for k in diff})
    r.update(unsupported=un, verdict=verdict, causal=causal, keys=keys,
             apply_blocked=bool(set(keys) & set(C.APPLY_BLOCKED_KEYS)))
    return r


def _pm(pol, kind, cat):
    import probe as PR
    return PR._pm(pol, kind, cat)


class Adapter:
    # legacy M2 adapter: full recorded window, full-record survival (look-ahead). causal = False.
    LIVE_OK = False

    def __init__(self, probe=None):
        if probe is None:
            import probe as PR
            probe = PR.V120cReplayProbe(metric=PR.MX.get("gp_per_h"))
        self.p = probe
        self.t0, self.t1 = probe.rec.T0, probe.rec.T1 + 1e-6
        self.base_h = probe.base_h

    def unsupported(self, base, cand):
        return unsupported_of(diff_keys(base, cand))

    def deltas(self, base, cand):
        return self.p._price_mult(base, cand)

    def __call__(self, base, cand):
        diff = diff_keys(base, cand)
        un = unsupported_of(diff)
        if un:
            return _finish(stats([], self.t0, self.t1, self.base_h), diff, un, "unsupported", False)
        r = stats(self.deltas(base, cand), self.t0, self.t1, self.base_h)
        return _finish(r, diff, un, "ok" if r["n"] else "no_evidence", False)


class CausalAdapter:
    LIVE_OK = True

    # judge at decision time t_d on [max(T0, t_d - lookback_s), t_d); survival from market rows with ts < t_d only.
    def __init__(self, probe, t_d, lookback_s=None):
        import buyer_state as BS
        import replay as RP
        self.p, self.rec, self.t_d, self.RP = probe, probe.rec, float(t_d), RP
        self.t0 = max(self.rec.T0, self.t_d - lookback_s) if lookback_s else self.rec.T0
        self.t1 = self.t_d
        self.bs = BS.BuyerState([m for m in self.rec.market if m["ts"] < self.t_d])
        self.bs.advance(self.t_d)
        cg = {}
        for m in self.rec.market:
            if m.get("ratio") is not None and self.rec.T0 <= m["ts"] < self.t_d:
                cg.setdefault((m["category"], m["grade"]), []).append(m["ratio"])
        self.cat_ratios = cg
        self.survival = probe.ev.survival
        self.sales = [s for s in self.rec.sales if self.t0 <= s["ts"] < self.t1]
        # P2: blocks = hours that contain recorded rows (sales or market) in [t0, t_d); staleness = time since the
        # last recorded row before t_d. Stale (> C.STALE_S) -> verdict "stale", never a win.
        self.data_ts = sorted([m["ts"] for m in self.rec.market if self.t0 <= m["ts"] < self.t1]
                              + [s["ts"] for s in self.sales])
        past = [m["ts"] for m in self.rec.market if m["ts"] < self.t_d] + [s["ts"] for s in self.rec.sales
                                                                           if s["ts"] < self.t_d]
        self.data_last = max(past) if past else None
        self.stale_s = None if self.data_last is None else self.t_d - self.data_last
        self.stale = self.stale_s is None or self.stale_s > C.STALE_S
        hours = data_hours(self.data_ts, self.t0, self.t1)
        self.base_h = (sum(RP.gp_of(s) for s in self.sales) / hours) if hours > 0 else None
        self.excluded = 0

    def accept_prob(self, s, r1):
        # replay.Eval.accept_prob with causal histories and no 1.0 fallback: None = no evidence
        r0 = s["amount"] / s["list_price"]
        xs = self.bs.price_ratios(s["buyer"], s["kind"], s["grade"])
        if xs is not None:
            xs = list(xs)
            if xs:
                xs.remove(min(xs, key=lambda x: abs(x - r0)))
            if len(xs) < 5:
                xs = None
        if xs is None:
            xs = list(self.cat_ratios.get((s["category"], s["grade"]), []))
            if xs:
                xs.remove(min(xs, key=lambda x: abs(x - r0)))
        if not xs:
            return None
        if r0 > max(xs) + 1e-12:
            return None
        return min(1.0, self.survival(xs, r1) / self.survival(xs, r0))

    def deltas(self, base, cand):
        out, self.excluded, cut = [], 0, False
        for s in self.sales:
            b, c = _pm(base, s["kind"], s["category"]), _pm(cand, s["kind"], s["category"])
            if b is None or c is None or b == c:
                continue
            k = c / b
            if k < 1:
                cut = True
                continue
            p1 = int(s["amount"] * k)
            P = self.accept_prob(s, p1 / s["list_price"])
            if P is None:
                self.excluded += 1
                continue
            # [N4] s["refunded"] comes from records.py over the WHOLE record (broke_at may be after t_d): structural
            # future information. CR REVIEW_M2r §5(b): 0 such refunds in the current data; kept, documented.
            kept = (p1 - s["cost"]) if not s["refunded"] else -s["cost"]
            out.append((s["ts"], P * kept - self.RP.gp_of(s), {"rev": P * p1 - s["amount"]}))
        return out, cut

    def __call__(self, base, cand):
        diff = diff_keys(base, cand)
        un = unsupported_of(diff)
        if un:
            r = stats([], self.t0, self.t1, self.base_h)
            return _finish(dict(r, excluded=0, t_d=self.t_d), diff, un, "unsupported", True)
        d, cut = self.deltas(base, cand)
        fresh = {"t_d": self.t_d, "data_last": self.data_last, "stale_s": self.stale_s, "stale": self.stale}
        if cut:
            r = stats([], self.t0, self.t1, self.base_h)
            return _finish(dict(r, excluded=self.excluded, **fresh), diff, un, "unjudgeable_cut", True)
        r = stats(d, self.t0, self.t1, self.base_h, data_ts=self.data_ts)
        verdict = "stale" if self.stale else ("ok" if r["n"] else "no_evidence")
        return _finish(dict(r, excluded=self.excluded, **fresh), diff, un, verdict, True)
