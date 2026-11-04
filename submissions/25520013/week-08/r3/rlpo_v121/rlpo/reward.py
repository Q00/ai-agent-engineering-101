# Reward and time-left discount (DESIGN_v3 sec 2 R, sec 3 gamma). Formula and constants are code; the LLM cannot
# change them (they are not policy paths; guard allowlist = C.PROPOSE_KEYS policy paths only; check_constants pin).
# Official formula: score/SCORE_FORMULA.md (CONFIRMED 14:3x KST, recompute error 0.000 on two boards).
import hashlib
import json

from rlpo import config as C

W_SHOP = {"profit": 0.5, "revenue": 0.3, "stars_rank": 0.2}
W_BUYER = {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}
TERMS = tuple(W_SHOP) + tuple(W_BUYER)
# M1 legacy 3-term proxy (profit, revenue, surplus). NOT the reward any more (DESIGN_v3 sec 2); kept for reference.
W_PROXY = {"profit": 0.5, "revenue": 0.3, "surplus": 0.5}
# Delay (s) from an action to the event that can move the term, per term (results_L/review_lag.json).
# profit/revenue/surplus/deals: 0 (counted at settle). stars_rank: 0 [estimate] - reviews are written at the deal:
#   our buyer 958/958 reviews within 0.84 s (p99) of its settle; other buyers reviews of OUR shop are not in the
#   box records; the board copies (13:42-14:08 KST) are after the record end, 0 matched. stars_rank is a shrunk
#   average over ~3k reviews (INFERRED), so one cycle moves it little: dilution, not lag.
# posts: 0 [estimate] (a post counts when posted). forum: None = NOT MEASURED (votes on posts arrive later; the
#   records hold each post once, no vote history). A term with lag None is never credited to an action.
LAG_S = {"profit": 0, "revenue": 0, "stars_rank": 0, "surplus": 0, "forum": None, "deals": 0, "posts": 0}


def pct(x, pop):
    # board percentile (mid-rank): (#below + 0.5 (#tied - 1)) / (n - 1); pop includes x; n < 2 -> None.
    # A missing value (None, e.g. buyer forum with no counted votes: 9/46 rows) ranks lowest, tied with the other
    # missing ones. Fit on both box boards: max error 0.00044 (buyers) / 0.0005 (shops) = board rounding (3 dp).
    def key(v):
        return (0, 0) if v is None else (1, v)
    vals = [key(v) for v in pop]
    k = key(x)
    if len(vals) < 2 or k not in vals:
        return None
    below = sum(v < k for v in vals)
    tied = sum(v == k for v in vals)
    return (below + 0.5 * (tied - 1)) / (len(vals) - 1)


def _w(W, p):
    if any((p or {}).get(k) is None for k in W):
        return None
    return sum(w * p[k] for k, w in W.items())


def shop_all(p):
    return _w(W_SHOP, p)


def buyer_all(p):
    return _w(W_BUYER, p)


def combined(p):
    # Players total = shop ALL + buyer ALL; p = {term: pct 0..1}; any missing term -> None (no silent drop)
    a, b = shop_all(p), buyer_all(p)
    return None if a is None or b is None else a + b


def r_t(p_prev, p_now):
    # main reward: change of the combined percentile total between two board observations
    a, b = combined(p_prev), combined(p_now)
    return None if a is None or b is None else b - a


def total_delta(score_prev, score_now):
    # board "total" field delta (same quantity as r_t when the board is consistent); None if missing
    a, b = (score_prev or {}).get("total"), (score_now or {}).get("total")
    return None if a is None or b is None else b - a


def proxy(z):
    # per-cycle proxy = the official combined total of per-term standings; every one of the 7 terms is required
    return combined(z)


def credited(p_prev, p_now, t_action, t_prev_obs, t_now_obs):
    # delay-aware credit for the action at t_action: term k counts only if t_prev_obs <= t_action and
    # t_now_obs >= t_action + GAMMA_DT_S + LAG_S[k]; unmeasured lag -> not credited (listed in missing)
    out, cov, miss = 0.0, 0.0, []
    for W in (W_SHOP, W_BUYER):
        for k, w in W.items():
            lag = LAG_S.get(k)
            a, b = (p_prev or {}).get(k), (p_now or {}).get(k)
            if lag is None or a is None or b is None or t_prev_obs > t_action or t_now_obs < t_action + C.GAMMA_DT_S + lag:
                miss.append(k)
                continue
            out += w * (b - a)
            cov += w
    return {"r": out if cov else None, "covered_w": cov, "missing": miss}


def gamma(t_rem):
    # gamma_t = 1 - dt / T_rem(t), floor C.GAMMA_FLOOR (0): effective horizon 1/(1-gamma) = T_rem/dt decision cycles.
    # dt and the floor are code constants (no argument can change them). Unknown or used-up time -> floor (myopic).
    if t_rem is None or t_rem <= 0:
        return C.GAMMA_FLOOR
    return max(C.GAMMA_FLOOR, 1.0 - C.GAMMA_DT_S / t_rem)


def gamma_at(t):
    # T_rem is measured to C.GAMMA_T_END = T_END (round end), not to the supply stop (DESIGN_v3 sec 3)
    return gamma(C.GAMMA_T_END - t)


def horizon_cycles(t):
    g = gamma_at(t)
    return 1.0 / (1.0 - g)


def const_sha():
    body = {"dt": C.GAMMA_DT_S, "floor": C.GAMMA_FLOOR, "t_end": C.GAMMA_T_END, "w_shop": W_SHOP, "w_buyer": W_BUYER,
            "lag": LAG_S}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def check_constants():
    # refuses to run with changed RL constants (gamma formula inputs, weights, lags) unless C.RL_CONST_SHA is re-pinned
    if const_sha() != C.RL_CONST_SHA:
        raise RuntimeError(f"RL constants changed: {const_sha()[:12]} != pinned {C.RL_CONST_SHA[:12]} (re-pin needs CR)")
    return True
