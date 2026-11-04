# C12 live entry point. Decision time t_d = the cycle time (now). Judged window = PAST [t_d - C.LIVE_LOOKBACK_S, t_d).
# Adapter = replay_adapter.CausalAdapter only (survival from rows before t_d, no 1.0 fallback, cuts unjudgeable).
# Refused here: replay_adapter.Adapter (look-ahead, LIVE_OK False) and adapter_m2b.Adapter (future window
# [t_d, t_d + H), LIVE_OK False; at t_d = T1 every key would get n = 0).
from rlpo import config as C
from rlpo import replay_adapter as RA


class LiveReplay:
    LIVE_OK = True

    def __init__(self, probe=None, lookback_s=None):
        if probe is None:
            import probe as PR
            probe = PR.V120cReplayProbe(metric=PR.MX.get("gp_per_h"))
        self.probe = probe
        self.lookback_s = C.LIVE_LOOKBACK_S if lookback_s is None else lookback_s

    def __call__(self, base, cand, t_d):
        ad = RA.CausalAdapter(self.probe, t_d, self.lookback_s)
        r = ad(base, cand)
        r["entry"] = "live_entry.LiveReplay/CausalAdapter"
        r["window"] = [ad.t0, ad.t1]
        return r


def check(replay):
    if getattr(replay, "LIVE_OK", None) is not True:
        raise ValueError(f"{type(replay).__name__} is not a live entry point (C12)")
    return True
