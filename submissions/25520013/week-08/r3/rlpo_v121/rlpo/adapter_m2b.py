"""M2b walk-forward adapter: __call__(base, cand) -> rlpo.guard.wins-compatible dict for decision time t_d,
window [t_d, t_d + H). Supported keys: replay_m2b.KEYS_STATUS. Placeholder warranty settings are read from
cand["rlpo_placeholders"] = {"warranty_on": bool, "warranty_exposure_cap_6h": number} (enabled=false in the
action space; replay only)."""
from rlpo import config as C
from rlpo import replay_m2b as M
from rlpo.replay_adapter import stats

SHOP = ("price_mult", "min_margin")
BUYER = ("open_frac", "max_pay_frac")


def _v(pol, side, kind, key):
    src = (pol.get("buyer") or {}) if side == "buyer" else pol
    return ((src.get("defaults") or {}).get(kind) or {}).get(key)


class Data:
    def __init__(self):
        import records
        import replay as RP
        import buyer_replay as BR
        self.rec = rec = records.load()
        self.RP, self.BR = RP, BR
        self.rows = M.open_rows(rec)
        self.buys, _ = BR.load_our(rec)
        self.idx = BR.ShopIdx(rec.market)
        ev = RP.Eval(rec)
        self.wlog = []
        self.wd = RP.f_warranty(rec, ev, "both", "lam", log=self.wlog)
        self.unit_price = {a["unit"]: a["price"] for a in rec.accepts}
        self.base_gp_h = sum(RP.gp_of(s) for s in rec.sales) / ((rec.T1 - rec.T0) / 3600)


class Adapter:
    LIVE_OK = False

    def __init__(self, data, t_d, H=None):
        self.D, self.t_d = data, t_d
        self.t1 = min(data.rec.T1 + 1e-6, t_d + H) if H else data.rec.T1 + 1e-6
        self.el = M.elasticity_report(data.rows, t_d, n_boot=100)
        self.beta, self.n_fit = M.fit_logit([r for r in data.rows if r["ts"] < t_d])
        # C12: fixed CR gate. price_ok stays False unless C.M2B_PRICE_OPEN (False; opening = CR gate) even when the
        # slope CI later turns negative. This adapter judges the FUTURE window [t_d, t_d + H) (walk-forward replay
        # only); it is not a live entry point: rlpo.live_entry refuses it (LIVE_OK = False).
        self.price_ok = (C.M2B_PRICE_OPEN and self.beta is not None and self.el.get("slope_ci") is not None and self.el["slope_ci"][1] < 0)

    def diffs(self, base, cand):
        out = []
        for kind in ("food", "goods"):
            for key in SHOP:
                if _v(base, "shop", kind, key) != _v(cand, "shop", kind, key):
                    out.append(("shop", kind, key))
            for key in BUYER + ("step_frac", "max_rounds"):
                if _v(base, "buyer", kind, key) != _v(cand, "buyer", kind, key):
                    out.append(("buyer", kind, key))
            for key in ("concession", "max_rounds", "stock_target", "qty_per_buy"):
                if _v(base, "shop", kind, key) != _v(cand, "shop", kind, key):
                    out.append(("shop", kind, key))
        if (base.get("rlpo_placeholders") or {}) != (cand.get("rlpo_placeholders") or {}):
            out.append(("shop", "goods", "warranty_on"))
        return out

    def deltas(self, base, cand):
        D, a, b = self.D, self.t_d, self.t1
        dl, unsupported, keys = [], [], []
        for side, kind, key in self.diffs(base, cand):
            keys.append(key)
            if key == "price_mult":
                if not self.price_ok:
                    unsupported.append(key)
                    continue
                rows = [r for r in D.rows if r["kind"] == kind]
                dl += M.d_price(rows, self.beta, lambda r: _v(base, "shop", kind, key), lambda r: _v(cand, "shop", kind, key), a, b)
            elif key == "min_margin":
                ss = [s for s in D.rec.sales if s["kind"] == kind]
                dl += M.d_min_margin(ss, _v(base, "shop", kind, key), _v(cand, "shop", kind, key), a, b)
            elif side == "buyer" and key == "open_frac":
                bs = [x for x in D.buys if x["kind"] == kind]
                dl += M.d_open(bs, D.idx, D.BR, _v(base, "buyer", kind, key), _v(cand, "buyer", kind, key), a, b)
            elif side == "buyer" and key == "max_pay_frac":
                bs = [x for x in D.buys if x["kind"] == kind]
                dl += M.d_cap(bs, D.idx, D.BR, _v(base, "buyer", kind, key), _v(cand, "buyer", kind, key), a, b)
            elif key == "warranty_on":
                ph = cand.get("rlpo_placeholders") or {}
                dl += M.d_warranty(D.wd, D.wlog, D.unit_price, bool(ph.get("warranty_on")),
                                   float(ph.get("warranty_exposure_cap_6h", 0)), a, b)
            else:
                unsupported.append(key)
        return dl, unsupported, keys

    def __call__(self, base, cand):
        dl, un, keys = self.deltas(base, cand)
        r = stats([] if un else dl, self.t_d, self.t1, self.D.base_gp_h)
        r.update(unsupported=un, keys=keys, t_d=self.t_d, t1=self.t1, n_fit=self.n_fit)
        return r
