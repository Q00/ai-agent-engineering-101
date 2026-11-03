"""R3 layer 2: incremental aggregates over the event ledger (SCHEMA.md v1.1). Owner: ML Engineer (R3-01b).

    from aggregates import Aggregates
    A = Aggregates.open(is_own=fn, catalog=cat)   # loads <R3_STATE>/aggregates.json if present
    A.update()                                    # reads only events after the saved byte offset (seq cursor)
    A.save()
    A.category(c) / A.sku(s) / A.shop(x) / A.me()  # SCHEMA.md section 5 interface (policy.py `stats`)

Per decision (review 11/2): use Resident (state kept in the process, checkpoint every CHECKPOINT_EVENTS events
or CHECKPOINT_S s) or, in a short-lived hook process, StatsView (category-sharded read views written at each
checkpoint). Neither reads or writes the whole snapshot per decision. A folder without a Resident (the buyer
folder) calls catch_up() once at process start (snapshot load + new events + checkpoint under a folder lock).
Public API: tests/results/R3-01_summary.md.

Cost: apply() is O(1) per event (a fixed number of dict updates; no loop over history). Queries are bounded by
the number of price buckets (<= ~1,000 per key, see below) or by the catalog size, never by the event count.
A full recompute (Aggregates() fed from offset 0) and an incremental run with save/load in between give the
same state (tests/test_aggregates.py).

Methods (stated so REPORT.md can cite them):
- Settle price quantiles per category and SKU: log-bucket histogram (DDSketch-style, relative accuracy
  ALPHA = 1%): a price p > 0 goes to bucket ceil(log(p)/log(GAMMA)), GAMMA = (1+ALPHA)/(1-ALPHA); a quantile is
  the representative value 2*GAMMA^i/(GAMMA+1) of the bucket holding rank q*(n-1), so its relative error is
  <= 1%. Order-independent (a count per bucket), hence exact equality between incremental and full runs.
  Count, sum, min, max are exact.
- Demand velocity: exponentially decayed count of settles, tau = VEL_TAU_H hours; velocity_per_h =
  sum_i exp(-(now - t_i)/tau) / tau (events per hour, recent-weighted). Also the lifetime rate n / span.
- Sell rate by hours left (food): for our own units, from restock (expires_at) to sale/expiry; each unit is
  "exposed" to every hours-left bucket between its first and last observed hours-left; sell_rate[b] =
  sold in b / exposed to b (a discrete hazard). Market-wide: the hours-left distribution of all food settles.
  The sale time of our unit is the ts of the settle of that unit if seen, else the unit_gone ingest time.
- Failure rate: broken / settles per category and SKU (goods break within 6 h; prior handled by policy.py).
- Shop reputation: lifetime counts and decayed counts with half-life REP_HALF_LIFE_H ("valid period"):
  claim_honest_rate = (honest + prior) / (claims + prior) (Laplace prior 2 as in reputation.py), broken_rate =
  broken / (settled + prior). claim_honest comes from the settle line (revealed after the sale).
- My P&L: shop sales (revenue, COGS from the unit's restock cost), expired write-offs, an estimated storage fee
  (STORAGE_PER_DAY of cost per day held; an estimate, not a market figure), refunds; buyer spend, surplus vs the
  catalog list price, budget surplus. Inventory days = live units / (decayed own sales per day).
  v1.13 (M13): storage_est_goods = the same estimate over goods units only (agent.md 140: goods pay storage; EL
  decision); storage_charged / storage_rows = market ledger `storage` rows for our shop (ledger.py, format assumed).
  net_profit_est deducts storage_charged when any row was seen, else storage_est_goods (v1.12: storage_est, every unit).

Exclusions:
- Self trade: buyer AND shop both ours (same GitHub owner) -> excluded from every table (count kept).
- SCHEMA.md 3: a settle where buyer OR shop is ours (data.own_owner, or the injected `is_own`) is left out of
  the market tables (price quantiles, velocity, market hours-left); it still counts for reputation (our buyer's
  purchases are the main source) and for my P&L.
- Our own shops are not rated in the reputation table.
- One settle counts once across sources (market / payments / deal) by data.ledger (else by order); a later
  copy that carries the claim fills the claim counters if the first copy had none.
- Breakage counts once per purchase (SCHEMA.md 3, v1.2): purchase id = order (else unit). A broken line with
  an order/unit (or broken.jsonl's settle `ledger` -> that settle's order) is "identified"; a market broken line
  without one is "anonymous" for its group (shop canon | sku | buyer). Per group, an identified break and an
  anonymous one cancel each other once (they are the two copies of the same break), in either arrival order;
  the same purchase id and the same market break seq count once. Category/SKU fail_rate = broken / all
  non-self-trade settles (ns), our own purchases included (a product-quality signal, not a price signal).
- My P&L (SCHEMA.md 3, FIFO): restock adds a cost layer (unit_cost, else cost) to the SKU's queue sorted by
  `fifo` = [sku, supplied_at, unit]. Each own-shop settle takes the layer of its `unit` if it is still queued
  (specific identification), else the front layer (FIFO), else counts cogs_unknown. unit_gone(expired) of a
  restocked unit writes off its own layer (else, if FIFO already gave it to a sale, the SKU's front layer); a unit
  never restocked in the ledger is written off at its event unit_cost without touching the queue. Inventory days = live units / decayed
  sales per day; inventory_cost = cost of the layers still queued.

Ownership is decided by an injected function is_own(name_or_url) -> bool. `owner_checker()` builds it from
r3/owner.py (`owner.cached`, no network) plus policy.json owner names plus R3_SHOP_NAME (v3); tests inject a mock.
"""
import hashlib
import json
import math
import os
import time
from collections import OrderedDict
from pathlib import Path

AGG_VERSION = 3                  # 2: FIFO cost layers, purchase-based breakage dedupe, SKU hash shards
                                 # 3: owner_checker adds R3_SHOP_NAME; snapshot keeps owner_fp (rebuild on change)
ALPHA = 0.01
GAMMA = (1 + ALPHA) / (1 - ALPHA)
LOG_GAMMA = math.log(GAMMA)
VEL_TAU_H = 24.0
REP_HALF_LIFE_H = 72.0
HL_EDGES = (6, 12, 24, 48, 96)          # hours-left buckets: <6, 6-12, 12-24, 24-48, 48-96, >=96
HL_LABELS = ("0-6", "6-12", "12-24", "24-48", "48-96", "96+")
STORAGE_PER_DAY = 0.01
MIN_RATE = 1e-4                  # sales/day below this (shown as 0.0) -> inventory_days None, not a huge number
SKU_SHARDS_HEX = 2               # views/sku/<sha1(sku)[:2]>.json: 256 SKU shards, no catalog needed
KEEP = 20_000                           # bounded dedupe maps (same bound as ledger.DEDUPE_KEEP)
PRIOR = 2
HERE = Path(__file__).resolve().parent
SNAPSHOT = "aggregates.json"


# --- small helpers (pure, O(1))

def bucket(p):
    return "z" if p <= 0 else str(math.ceil(math.log(p) / LOG_GAMMA))


def bucket_value(k):
    return 0.0 if k == "z" else 2 * GAMMA ** int(k) / (GAMMA + 1)


def hl_bucket(hours):
    for i, e in enumerate(HL_EDGES):
        if hours < e:
            return i
    return len(HL_EDGES)


def decay_add(dc, t, x, tau_s):
    """dc = [value, ref_t]: value is sum x_i*exp(-(ref_t - t_i)/tau). Works for out-of-order t."""
    v, ref = dc
    if ref is None or t >= ref:
        dc[0] = (v * math.exp(-(t - ref) / tau_s) if ref is not None else 0.0) + x
        dc[1] = t
    else:
        dc[0] = v + x * math.exp(-(ref - t) / tau_s)


def decay_at(dc, now, tau_s):
    v, ref = dc
    if ref is None:
        return 0.0
    return v * math.exp(-max(0.0, now - ref) / tau_s)


def _price_blank():
    return {"n": 0, "sum": 0, "min": None, "max": None, "h": {}, "vel": [0.0, None], "first": None, "last": None,
            "broken": 0, "ns": 0}


def _price_add(st, p, t):
    st["n"] += 1
    st["sum"] += p
    st["min"] = p if st["min"] is None else min(st["min"], p)
    st["max"] = p if st["max"] is None else max(st["max"], p)
    k = bucket(p)
    st["h"][k] = st["h"].get(k, 0) + 1
    decay_add(st["vel"], t, 1.0, VEL_TAU_H * 3600)
    st["first"] = t if st["first"] is None else min(st["first"], t)
    st["last"] = t if st["last"] is None else max(st["last"], t)


def quantiles(h, n, qs=(0.25, 0.5, 0.75)):
    """Quantiles from a bucket histogram (cost: number of non-empty buckets, not events)."""
    if not n:
        return {q: None for q in qs}
    keys = sorted(h, key=lambda k: -1e18 if k == "z" else int(k))
    out, acc, i = {}, 0, 0
    targets = sorted((q * (n - 1), q) for q in qs)
    for k in keys:
        acc += h[k]
        while i < len(targets) and targets[i][0] < acc:
            out[targets[i][1]] = int(round(bucket_value(k)))
            i += 1
    for _, q in targets[i:]:
        out[q] = int(round(bucket_value(keys[-1])))
    return out


def _bounded(d, keep=KEEP):
    while len(d) > keep:
        d.pop(next(iter(d)))


def _sortable(x):
    """FIFO key part: numbers as fixed-width strings so they sort numerically and stay JSON-stable."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return f"{float(x):020.6f}"
    return str(x if x is not None else "")


def _low(x):
    return str(x or "").strip().lower().rstrip("/")


def _owner_mod():
    try:
        import owner as OW
        OW.norm_name, OW.norm_url          # noqa: B018: the v1.2 API this module uses
        return OW
    except Exception:                      # noqa: BLE001: owner.py missing or changed
        return None


def norm_buyer(x):
    """Name normalisation for buyer/shop names (owner.norm_name: case, spaces, `_`/`-` equal), _low if no owner.py."""
    OW = _owner_mod()
    return OW.norm_name(x) if OW else _low(x)


def owner_checker(policy_path=None, state=None, shop_name=None):
    """is_own(name_or_url) with r3/owner.py v1.2: names compared after owner.norm_name (case, spaces, `_`/`-`
    equal; a github.com URL becomes its login), URLs after owner.norm_url. Sources: owner.cached (policy
    own_shop_names + the last /api/shops cache, no network) plus policy owner.github / own_buyer_names, plus
    our running shop's name: `shop_name`, else env R3_SHOP_NAME (read once, here; the same variable
    ledger.Ledger.me_shop, volume.py and hooks/seller.py use; owner.py itself does not read it).
    R3_BUYER_NAME is NOT added: a settle whose buyer AND shop are both ours is a self trade and is dropped.
    If owner.py cannot be imported: policy names + shop name only, compared with _low.
    The returned function carries `.fp` (sha256 of the sorted id set); a snapshot built with another set is
    rebuilt (Aggregates.load)."""
    p = Path(policy_path or os.environ.get("R3_POLICY", HERE / "policy.json"))
    try:
        pol = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pol = {}
    o = pol.get("owner") or {}
    me_shop = shop_name if shop_name is not None else os.environ.get("R3_SHOP_NAME")
    raw = [n for n in [o.get("github"), me_shop] + list(o.get("own_shop_names") or []) +
           list(o.get("own_buyer_names") or []) if n]
    OW = _owner_mod()
    if OW is None:
        ids = {_low(n) for n in raw} - {""}

        def is_own_low(x):
            return bool(x) and _low(x) in ids
        is_own_low.fp = _owner_fp(ids, ())
        return is_own_low
    names = {OW.norm_name(n) for n in raw} - {""}
    urls = set()
    try:
        n2, u2 = OW.cached(pol, state)
        names |= {OW.norm_name(n) for n in n2} - {""}
        urls |= {OW.norm_url(u) for u in u2} - {""}
    except Exception:            # noqa: BLE001: unreadable cache -> policy names only
        pass

    def is_own(x):
        if not x:
            return False
        return OW.norm_name(x) in names or OW.norm_url(x) in urls or OW.norm_name(x) in urls
    is_own.fp = _owner_fp(names, urls)
    return is_own


def _owner_fp(names, urls):
    return hashlib.sha256(json.dumps([sorted(names), sorted(urls)], ensure_ascii=False).encode()).hexdigest()[:16]


class Aggregates:
    def __init__(self, root=None, is_own=None, catalog=None, state=None):
        self.root = Path(root or os.environ.get("R3_STATE", ".r3"))
        self.is_own = is_own or (lambda x: False)
        self.catalog = catalog or {}
        self.s = state or self.blank()
        if state is None:
            self.s["owner_fp"] = getattr(self.is_own, "fp", None)   # None for injected checkers (tests)
        self.touched = None          # Resident sets {"cat": set(), "sku": set()}: shards to rewrite at checkpoint

    def _cat(self, c):
        if self.touched is not None:
            self.touched["cat"].add(c)
        return self.s["cat"].setdefault(c, _price_blank())

    def _skust(self, k):
        if self.touched is not None:
            self.touched["sku"].add(k)
        return self.s["sku"].setdefault(k, _price_blank())

    # --- state
    @staticmethod
    def blank():
        return {"version": AGG_VERSION, "off": 0, "seq": 0, "events": 0, "types": {},
                "excluded": {"self_trade": 0, "own_market_stats": 0, "dup_settle": 0, "dup_broken": 0,
                             "own_shop_rating": 0, "no_price": 0},
                "cat": {}, "sku": {}, "skuinfo": {}, "shop": {}, "alias": {},
                "seen": OrderedDict(), "bseen": OrderedDict(), "buys": OrderedDict(), "refs": OrderedDict(),
                "bgrp": OrderedDict(), "units": {}, "fifo": {},
                "hl": {}, "mkt_hl": {}, "me": {"sales": 0, "revenue": 0, "cogs": 0, "cogs_unknown": 0,
                                               "restocked": 0, "restock_cost": 0, "expired": 0, "expired_cost": 0,
                                               "storage_est": 0.0, "storage_est_goods": 0.0, "storage_charged": 0, "storage_rows": 0, "refunds": 0, "refund_amount": 0,
                                               "buys": 0, "spend": 0, "surplus_list": 0, "surplus_budget": 0,
                                               "sold_vel": [0.0, None], "inventory_cost": 0, "revenue_costed": 0,
                                               "cogs_specific": 0, "cogs_fifo": 0, "writeoff_unqueued": 0},
                "rejects": {}, "fails": {}, "trades": 0, "reviews": {"n": 0, "stars": 0}, "last_ts": None}

    @classmethod
    def open(cls, root=None, is_own=None, catalog=None):
        a = cls(root, is_own, catalog)
        a.load()
        return a

    def snapshot_path(self):
        return self.root / SNAPSHOT

    def save(self, path=None):
        p = Path(path or self.snapshot_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
        return p

    def load(self, path=None):
        p = Path(path or self.snapshot_path())
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if s.get("version") != AGG_VERSION:
            return False                             # incompatible snapshot: rebuild from offset 0
        if s.get("owner_fp") != getattr(self.is_own, "fp", None):
            return False                             # built with another own-shop set (e.g. R3_SHOP_NAME): rebuild
        for k in ("seen", "bseen", "buys", "refs", "bgrp"):
            s[k] = OrderedDict(s[k])
        self.s = s
        return True

    def digest(self):
        """sha256 of the canonical state (used by the equality tests and the result CSVs)."""
        return hashlib.sha256(json.dumps(self.s, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()

    # --- input
    def update(self, ledger=None):
        """Apply every ledger event after the saved byte offset. Returns the number applied."""
        if ledger is None:
            from ledger import Ledger
            ledger = Ledger(self.root)
        n = 0
        for off, ev in ledger.iter_from(self.s["off"]):
            self.apply(ev)
            self.s["off"] = off
            n += 1
        return n

    def _info(self, sku, d=None):
        """(category, kind, list_price) from the event, the remembered SKU info, or the catalog."""
        d = d or {}
        si = self.s["skuinfo"].get(sku) if sku else None
        c = self.catalog.get(sku) if sku else None
        cat = d.get("category") or (si and si[0]) or (c and c.get("category"))
        kind = d.get("kind") or (si and si[1]) or (c and c.get("kind"))
        lp = d.get("list_price") or (si and si[2]) or (c and c.get("list_price"))
        if sku and (cat or kind or lp) and (not si or si != [cat, kind, lp]):
            self.s["skuinfo"][sku] = [cat, kind, lp]
        return cat, kind, lp

    def apply(self, ev):
        s = self.s
        if ev.get("seq", 0) <= s["seq"]:
            return False                             # already applied (cursor rewound by a crash): idempotent
        s["seq"] = ev["seq"]
        s["events"] += 1
        t = ev.get("type")
        s["types"][t] = s["types"].get(t, 0) + 1
        ts = ev.get("ts") or 0
        s["last_ts"] = ts if s["last_ts"] is None else max(s["last_ts"], ts)
        d = ev.get("data") or {}
        f = getattr(self, "_on_" + str(t), None)
        if f:
            f(ev, d, ts)
        return True

    # --- handlers (each O(1))
    def _on_settle(self, ev, d, ts):
        s = self.s
        key = f"L{d['ledger']}" if d.get("ledger") is not None else f"O{d.get('order')}"
        shop, buyer = d.get("shop"), d.get("buyer")
        has_claim = d.get("claim") is not None and d.get("claim_honest") is not None
        if key in s["seen"]:
            s["excluded"]["dup_settle"] += 1
            rec = s["seen"][key]
            if shop and rec[0] and _low(shop) != _low(rec[0]) and _low(shop) not in s["alias"]:
                s["alias"][_low(shop)] = _low(rec[0])
            self._note_buy(d)
            if has_claim and not rec[1] and rec[0]:           # first copy had no claim: fill it now
                rec[1] = True
                self._rep_claim(rec[0], bool(d.get("claim_honest")), rec[2])
            return
        mine_buy = ev.get("src") in ("payments", "deal") or self.is_own(buyer)
        mine_shop = self.is_own(shop)
        self._note_buy(d)
        if mine_buy and mine_shop:
            s["excluded"]["self_trade"] += 1
            s["seen"][key] = [None, True, ts]
            _bounded(s["seen"])
            return
        sku = d.get("sku") or d.get("item")
        cat, kind, lp = self._info(sku, d)
        amt = d.get("amount") if d.get("amount") is not None else d.get("price")
        canon = None
        if shop and not mine_shop:
            canon = s["alias"].get(_low(shop), _low(shop))
            r = self._rep(canon)
            r["settled"] += 1
            decay_add(r["d_settled"], ts, 1.0, self._rep_tau())
            if has_claim:
                self._rep_claim(canon, bool(d.get("claim_honest")), ts)
        elif shop:
            s["excluded"]["own_shop_rating"] += 1
        s["seen"][key] = [canon, has_claim, ts, cat]
        _bounded(s["seen"])
        if cat:
            self._cat(cat)["ns"] += 1
        if sku:
            self._skust(sku)["ns"] += 1
        if isinstance(amt, int):
            if d.get("own_owner") or mine_buy or mine_shop:
                s["excluded"]["own_market_stats"] += 1
            else:
                if cat:
                    _price_add(self._cat(cat), amt, ts)
                if sku:
                    _price_add(self._skust(sku), amt, ts)
                if d.get("expires_at"):
                    hb = HL_LABELS[hl_bucket((d["expires_at"] - ts) / 3600)]
                    m = s["mkt_hl"].setdefault(cat or "?", {})
                    if self.touched is not None:
                        self.touched["cat"].add(cat or "?")
                    m[hb] = m.get(hb, 0) + 1
        else:
            s["excluded"]["no_price"] += 1
        me = s["me"]
        if mine_shop and isinstance(amt, int):
            me["sales"] += 1
            me["revenue"] += amt
            decay_add(me["sold_vel"], ts, 1.0, VEL_TAU_H * 3600)
            u = s["units"].get(d.get("unit"))
            if u:
                u["sold_at"] = ts
            how, cost = self._take_layer((u and u["sku"]) or sku, d.get("unit"))   # a known unit decides the SKU
            if how:
                me["cogs"] += cost
                me["revenue_costed"] += amt
                me["cogs_" + how] += cost
            else:
                me["cogs_unknown"] += 1
        if mine_buy and isinstance(amt, int):
            me["buys"] += 1
            me["spend"] += amt
            if lp:
                me["surplus_list"] += lp - amt
            if isinstance(d.get("budget"), int):
                me["surplus_budget"] += d["budget"] - amt

    def _rep_tau(self):
        return REP_HALF_LIFE_H * 3600 / math.log(2)

    def _rep(self, canon):
        return self.s["shop"].setdefault(canon, {"settled": 0, "claims": 0, "honest": 0, "broken": 0,
                                                 "d_settled": [0.0, None], "d_claims": [0.0, None],
                                                 "d_honest": [0.0, None], "d_broken": [0.0, None], "last": None})

    def _rep_claim(self, canon, honest, ts):
        r = self._rep(canon)
        r["claims"] += 1
        r["honest"] += honest
        decay_add(r["d_claims"], ts, 1.0, self._rep_tau())
        decay_add(r["d_honest"], ts, 1.0 if honest else 0.0, self._rep_tau())
        r["last"] = ts if r["last"] is None else max(r["last"], ts)

    # --- purchases and breakage (one break per purchase)
    @staticmethod
    def _buy_id(d):
        if d.get("order"):
            return f"order:{d['order']}"
        if d.get("unit"):
            return f"unit:{d['unit']}"
        return None

    def _note_buy(self, d):
        """Remember a purchase (any settle copy): id -> [shop key, sku, buyer]; refs L<settle seq>/U<unit> -> id.
        Later copies fill fields the first one lacked (payments has no buyer). Bounded by KEEP."""
        bid = self._buy_id(d)
        if not bid:
            return
        s = self.s
        b = s["buys"].get(bid)
        new = [_low(d.get("shop")) or None, d.get("sku") or d.get("item"), norm_buyer(d.get("buyer")) or None]
        if b is None:
            s["buys"][bid] = new
            _bounded(s["buys"])
        else:
            for i in range(3):
                if b[i] is None and new[i] is not None:
                    b[i] = new[i]
        for r in ((f"L{d['ledger']}" if d.get("ledger") is not None else None),
                  (f"U{d['unit']}" if d.get("unit") else None)):
            if r and r not in s["refs"]:
                s["refs"][r] = bid
                _bounded(s["refs"])

    def _on_broken(self, ev, d, ts):
        s = self.s
        src = ev.get("src")
        if src == "broken" and not d.get("attributed"):
            s["types"]["broken_unattributed"] = s["types"].get("broken_unattributed", 0) + 1
            return
        if src != "broken" and d.get("ledger") is not None:            # one market break line = one seq
            mk = f"M{d['ledger']}"
            if mk in s["bseen"]:
                s["excluded"]["dup_broken"] += 1
                return
            s["bseen"][mk] = 1
            _bounded(s["bseen"])
        bid = (f"order:{d['order']}" if d.get("order") else
               (s["refs"].get(f"U{d['unit']}") or f"unit:{d['unit']}") if d.get("unit") else
               s["refs"].get(f"L{d['ledger']}") if src == "broken" and d.get("ledger") is not None else None)
        b = s["buys"].get(bid) if bid else None
        shop = (b and b[0]) or d.get("shop")             # the purchase's own shop key first (alias-safe)
        sku = d.get("sku") or d.get("item") or (b and b[1])
        buyer = norm_buyer(d.get("buyer")) or (b and b[2]) or ""
        canon = s["alias"].get(_low(shop), _low(shop)) if shop else ""
        g = f"{canon}|{sku}|{buyer}"
        c = s["bgrp"].get(g)
        if c is None:
            c = s["bgrp"][g] = [0, 0]                      # [anonymous not yet matched, identified not yet matched]
            _bounded(s["bgrp"])
        if bid:
            if bid in s["bseen"]:
                s["excluded"]["dup_broken"] += 1
                return
            s["bseen"][bid] = 1
            _bounded(s["bseen"])
            if c[0] > 0:                                   # the market line of this break was counted already
                c[0] -= 1
                s["excluded"]["dup_broken"] += 1
                return
            c[1] += 1
        else:
            if c[1] > 0:                                   # broken.jsonl / identified copy came first
                c[1] -= 1
                s["excluded"]["dup_broken"] += 1
                return
            c[0] += 1
        ours = src == "broken" or self.is_own(buyer) or self.is_own(d.get("buyer"))
        if ours and self.is_own(shop):
            s["excluded"]["self_trade"] += 1
            return
        at = d.get("broke_at") or ts
        cat, _, _ = self._info(sku, d)
        if cat:
            self._cat(cat)["broken"] += 1
        if sku:
            self._skust(sku)["broken"] += 1
        if shop and not self.is_own(shop):
            r = self._rep(canon)
            r["broken"] += 1
            decay_add(r["d_broken"], at, 1.0, self._rep_tau())

    # --- FIFO cost layers
    def _take_layer(self, sku, uid=None):
        """-> ("specific"|"fifo"|None, cost). Removes the layer of `uid` if queued, else the front layer of sku."""
        q = self.s["fifo"].get(sku) if sku else None
        if not q:
            return None, 0
        i = next((k for k, x in enumerate(q) if x[2] == uid), None) if uid else None   # q <= units of one SKU
        how = "specific" if i is not None else "fifo"
        lay = q.pop(i if i is not None else 0)
        if not q:
            del self.s["fifo"][sku]
        self.s["me"]["inventory_cost"] -= lay[3]
        return how, lay[3]

    def _on_restock(self, ev, d, ts):
        s = self.s
        uid = d.get("unit")
        if not uid or uid in s["units"]:
            return
        sku = d.get("sku")
        cat, kind, _ = self._info(sku, d)
        uc = d.get("unit_cost") if isinstance(d.get("unit_cost"), int) else d.get("cost")
        cost = uc if isinstance(uc, int) else 0
        t0 = d.get("supplied_at") or ts
        s["units"][uid] = {"sku": sku, "cat": cat, "kind": kind, "cost": cost, "t0": t0,
                           "exp": d.get("expires_at"), "sold_at": None}
        fk = d.get("fifo") if isinstance(d.get("fifo"), list) and len(d["fifo"]) == 3 else [sku, t0, uid]
        lay = [str(fk[0]), _sortable(fk[1]), str(fk[2]), cost]
        q = s["fifo"].setdefault(sku, [])
        k = len(q)
        while k and q[k - 1][:3] > lay[:3]:            # arrivals are almost always in order: O(1) amortised
            k -= 1
        q.insert(k, lay)
        s["me"]["restocked"] += 1
        s["me"]["restock_cost"] += cost
        s["me"]["inventory_cost"] += cost

    def _on_unit_gone(self, ev, d, ts):
        s = self.s
        me = s["me"]
        u = s["units"].pop(d.get("unit"), None)
        expired = d.get("why") == "expired"
        if expired:
            # a known unit: its own layer, else (FIFO already gave that layer to a sale) the front layer of its SKU
            how, cost = self._take_layer(u["sku"], d.get("unit")) if u else (None, 0)
            if not how:                                    # never queued (restocked before the ledger began)
                uc = d.get("unit_cost") if isinstance(d.get("unit_cost"), int) else d.get("cost")
                cost = uc if isinstance(uc, int) else (u["cost"] if u else 0)
                me["writeoff_unqueued"] += 1
            me["expired"] += 1
            me["expired_cost"] += cost
        if u is None:
            return
        end = u["sold_at"] or ts
        fee = u["cost"] * STORAGE_PER_DAY * max(0.0, end - u["t0"]) / 86400
        me["storage_est"] = round(me["storage_est"] + fee, 6)          # every unit (v1.3 definition, kept)
        if u.get("kind") != "food":                    # v1.13 (EL decision): agent.md 140 charges goods only
            me["storage_est_goods"] = round(me.get("storage_est_goods", 0.0) + fee, 6)
        if u["exp"] and u["kind"] == "food":
            h0 = (u["exp"] - u["t0"]) / 3600
            h1 = max(0.0, (u["exp"] - end) / 3600)
            st = s["hl"].setdefault(u["cat"] or "?", {"exposed": [0] * len(HL_LABELS), "sold": [0] * len(HL_LABELS)})
            if self.touched is not None:
                self.touched["cat"].add(u["cat"] or "?")
            for b in range(hl_bucket(h1), hl_bucket(h0) + 1):     # at most 6 buckets: O(1)
                st["exposed"][b] += 1
            if not expired:
                st["sold"][hl_bucket(h1)] += 1

    def _on_storage(self, ev, d, ts):
        me = self.s["me"]
        me["storage_charged"] = me.get("storage_charged", 0) + int(d.get("amount") or 0)
        me["storage_rows"] = me.get("storage_rows", 0) + 1

    def _on_reject(self, ev, d, ts):
        c = d.get("reason_code") or "other"
        self.s["rejects"][c] = self.s["rejects"].get(c, 0) + 1

    def _on_fail(self, ev, d, ts):
        st = d.get("stage") or "?"
        self.s["fails"][st] = self.s["fails"].get(st, 0) + 1
        if st == "refund" and isinstance(d.get("detail"), dict):
            det = d["detail"]
            if self.is_own(det.get("shop")):
                self.s["me"]["refunds"] += 1
                if isinstance(det.get("amount"), int):
                    self.s["me"]["refund_amount"] += det["amount"]

    def _on_trade(self, ev, d, ts):
        self.s["trades"] += 1

    def _on_review(self, ev, d, ts):
        if isinstance(d.get("stars"), (int, float)):
            self.s["reviews"]["n"] += 1
            self.s["reviews"]["stars"] += d["stars"]

    # --- queries (SCHEMA.md section 5)
    def now(self):
        return self.s["last_ts"] or 0

    def _price_view(self, st, now):
        q = quantiles(st["h"], st["n"])
        if st["n"]:                                   # bucket midpoints can fall outside the exact min/max
            q = {k: None if v is None else max(st["min"], min(st["max"], v)) for k, v in q.items()}
        span_h = ((st["last"] or 0) - (st["first"] or 0)) / 3600
        return {"n": st["n"], "p25": q[0.25], "p50": q[0.5], "p75": q[0.75], "min": st["min"], "max": st["max"],
                "mean": round(st["sum"] / st["n"], 2) if st["n"] else None,
                "velocity_per_h": round(decay_at(st["vel"], now, VEL_TAU_H * 3600) / VEL_TAU_H, 6),
                "lifetime_per_h": round(st["n"] / span_h, 6) if span_h > 0 else None,
                "broken": st["broken"], "settles_all": st.get("ns", 0),
                "fail_rate": round(st["broken"] / st["ns"], 6) if st.get("ns") else None}

    def category(self, category, now=None):
        st = self.s["cat"].get(category)
        if st is None and category not in self.s["hl"] and category not in self.s["mkt_hl"]:
            return None
        out = self._price_view(st or _price_blank(), now or self.now())
        h = self.s["hl"].get(category)
        out["sell_rate_by_hours_left"] = {HL_LABELS[i]: (round(h["sold"][i] / h["exposed"][i], 4) if h["exposed"][i] else None)
                                          for i in range(len(HL_LABELS))} if h else {}
        out["market_sales_by_hours_left"] = dict(self.s["mkt_hl"].get(category) or {})
        return out

    def sku(self, sku, now=None):
        st = self.s["sku"].get(sku)
        return None if st is None else self._price_view(st, now or self.now())

    def shop(self, url_or_name, now=None):
        k = _low(url_or_name)
        r = self.s["shop"].get(self.s["alias"].get(k, k))
        if r is None:
            return None
        now = now or self.now()
        tau = self._rep_tau()
        ds, dc, dh, db = (decay_at(r[x], now, tau) for x in ("d_settled", "d_claims", "d_honest", "d_broken"))
        return {"settled": r["settled"], "claims": r["claims"], "broken": r["broken"],
                "claim_honest_rate": round((r["honest"] + PRIOR) / (r["claims"] + PRIOR), 6),
                "broken_rate": round(r["broken"] / (r["settled"] + PRIOR), 6),
                "recent_settled": round(ds, 4),
                "recent_claim_honest_rate": round((dh + PRIOR) / (dc + PRIOR), 6),
                "recent_broken_rate": round(db / (ds + PRIOR), 6), "half_life_h": REP_HALF_LIFE_H}

    def reputation_table(self, now=None):
        """shop -> stats, for policy.choose_offers (same keys as reputation.Reputation.table, aliases included).
        Uses the recent (decayed) rates. Cost: number of shops."""
        out = {k: self.shop(k, now) for k in self.s["shop"]}
        for v in out.values():
            v.update(claim_honest_rate=v["recent_claim_honest_rate"], broken_rate=v["recent_broken_rate"])
        for a, c in self.s["alias"].items():
            if c in out:
                out[a] = out[c]
        return out

    def me(self, now=None):
        m = dict(self.s["me"])
        now = now or self.now()
        per_day = decay_at(m.pop("sold_vel"), now, VEL_TAU_H * 3600) / VEL_TAU_H * 24
        live = len(self.s["units"])
        m["live_units"] = live
        m["sales_per_day"] = round(per_day, 4)
        m["inventory_days"] = round(live / per_day, 2) if per_day >= MIN_RATE else None
        m["gross_profit"] = m["revenue"] - m["cogs"]               # cogs_unknown sales carry no cost here
        m["gross_profit_costed"] = m["revenue_costed"] - m["cogs"]  # only sales with a known (FIFO/specific) cost
        # v1.13 (M13): storage the market charged (ledger `storage` rows) when there are any, else the goods-only
        # estimate (EL decision: agent.md 140 charges goods; storage_est over every unit stays as a v1.3 field)
        m.setdefault("storage_charged", 0)
        m.setdefault("storage_rows", 0)
        m.setdefault("storage_est_goods", 0.0)
        st = m["storage_charged"] if m["storage_rows"] else m["storage_est_goods"]
        m["net_profit_est"] = round(m["revenue"] - m["cogs"] - m["expired_cost"] - st - m["refund_amount"], 2)
        return m

    def cat_rank(self, now=None):
        """Categories with settles, fastest first (velocity_per_h desc, then name). Cost: number of categories
        (<= catalog categories, 140), no price buckets read. Used by cards.pick_categories."""
        now = now or self.now()
        tau = VEL_TAU_H * 3600
        return sorted(self.s["cat"], key=lambda c: (-round(decay_at(self.s["cat"][c]["vel"], now, tau) / VEL_TAU_H, 6), c))

    def summary(self):
        s = self.s
        return {"seq": s["seq"], "off": s["off"], "events": s["events"], "types": dict(s["types"]),
                "excluded": dict(s["excluded"]), "categories": len(s["cat"]), "skus": len(s["sku"]),
                "shops": len(s["shop"]), "rejects": dict(s["rejects"]), "fails": dict(s["fails"])}


# --- per-decision cost independent of the ledger length (R3-01b review, 11/2)

CHECKPOINT_EVENTS = 500          # Resident: checkpoint after this many applied events ...
CHECKPOINT_S = 30.0              # ... or this many seconds with at least one applied event
VIEWS = "views"                  # <root>/views/core.json, stamp.json, cat/<sha1(category)[:16]>.json, sku/<sha1(sku)[:2]>.json


def _shard_name(cat):
    return hashlib.sha1(str(cat or "").encode()).hexdigest()[:16] + ".json"


def _sku_shard(sku):
    """SKU tables are sharded by the SKU itself (not its category), so a reader needs no catalog (11/2 fix)."""
    return hashlib.sha1(str(sku or "").encode()).hexdigest()[:SKU_SHARDS_HEX] + ".json"


STAMP = "stamp.json"             # views/stamp.json: {version, seq} of the snapshot the views were written with


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class _folder_lock:
    """Non-blocking exclusive flock on <root>/aggregates.lock (yields False when another process holds it)."""

    def __init__(self, root):
        self.p = Path(root) / "aggregates.lock"
        self.f = None

    def __enter__(self):
        import fcntl
        self.p.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.p, "a")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close()
            self.f = None
            return False

    def __exit__(self, *a):
        if self.f:
            import fcntl
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()


def _write_atomic(p, text):
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


class Resident:
    """Aggregates kept in one long-lived process (e.g. the seller, or a small aggregator loop).

        R = Resident(root, is_own, catalog)     # one snapshot load at process start (R.startup_ms)
        R.refresh()                             # per decision: apply only the new ledger events (O(new events))
        R.A.category(c) ... cards.context_card(R.A, ...)
        R.maybe_checkpoint()                    # after answering: saves when CHECKPOINT_EVENTS / CHECKPOINT_S hit
        R.close()                               # final checkpoint

    Per decision there is no snapshot read or write. A checkpoint writes the full snapshot (crash recovery: the
    next start loads it and replays the ledger after its byte cursor; apply() is idempotent by seq) and the
    category- and SKU-sharded read views for short-lived hook processes (StatsView), rewriting only shards that changed.
    Single writer: only one Resident per state directory should checkpoint."""

    def __init__(self, root=None, is_own=None, catalog=None, every_events=CHECKPOINT_EVENTS, every_s=CHECKPOINT_S,
                 views=True, clock=time.monotonic):
        t = time.perf_counter()
        self.A = Aggregates(root, is_own, catalog)
        self.loaded = self.A.load()
        self.startup_ms = (time.perf_counter() - t) * 1000
        from ledger import Ledger
        self.ledger = Ledger(self.A.root)
        self.every_events, self.every_s, self.views, self.clock = every_events, every_s, views, clock
        self.pending = 0
        self.last_cp = clock()
        self.checkpoints = 0
        self.skipped_locked = 0
        self._shard_bytes = {}
        self._sku_groups = None
        self.A.touched = {"cat": set(), "sku": set()}
        st = _read_json(self.A.root / VIEWS / STAMP)       # views written for exactly this snapshot?
        self._views_full = not (self.loaded and st and st.get("version") == AGG_VERSION and st.get("seq") == self.A.s["seq"])

    def refresh(self):
        n = self.A.update(self.ledger)
        self.pending += n
        return n

    def due(self):
        return self.pending >= self.every_events or (self.pending > 0 and self.clock() - self.last_cp >= self.every_s)

    def maybe_checkpoint(self):
        if self.due():
            return self.checkpoint()            # False if another process holds the folder lock (retried later)
        return False

    def checkpoint(self):
        """Views first, then the snapshot (a crash in between leaves views ahead of the snapshot; the replay after
        restart touches the same shards again). Single writer per folder: a non-blocking lock on
        <root>/aggregates.lock; if another process holds it, nothing is written and False is returned."""
        with _folder_lock(self.A.root) as ok:
            if not ok:
                self.skipped_locked += 1
                return False
            if self.views:
                self.write_views()
            self.A.save()
        self.pending = 0
        self.last_cp = self.clock()
        self.checkpoints += 1
        return True

    def close(self):
        self.refresh()
        if self.pending or not self.A.snapshot_path().exists():
            self.checkpoint()

    def write_views(self):
        """Rewrite the category / SKU shards touched since the last checkpoint (all of them after a cold start
        whose views do not match the snapshot), then core.json and the stamp."""
        A, s = self.A, self.A.s
        d = A.root / VIEWS
        (d / "cat").mkdir(parents=True, exist_ok=True)
        (d / "sku").mkdir(parents=True, exist_ok=True)
        cats = set(s["cat"]) | set(s["hl"]) | set(s["mkt_hl"])
        if self._sku_groups is None:                   # shard -> skus, built once per process (O(SKUs))
            self._sku_groups = {}
            for k in s["sku"]:
                self._sku_groups.setdefault(_sku_shard(k), set()).add(k)
        else:
            for k in A.touched["sku"]:
                self._sku_groups.setdefault(_sku_shard(k), set()).add(k)
        full = self._views_full
        for c in (cats if full else A.touched["cat"] & cats):
            body = json.dumps({"cat": c, "st": s["cat"].get(c), "hl": s["hl"].get(c), "mkt_hl": s["mkt_hl"].get(c)},
                              ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            self._put(d / "cat" / _shard_name(c), ("cat", c), body)
        names = self._sku_groups if full else {_sku_shard(k) for k in A.touched["sku"]}
        for name in names:
            ks = self._sku_groups[name]
            body = json.dumps({"sku": {k: s["sku"][k] for k in sorted(ks)}}, ensure_ascii=False,
                              separators=(",", ":"), sort_keys=True)
            self._put(d / "sku" / name, ("sku", name), body)
        core = {k: s[k] for k in ("version", "off", "seq", "events", "types", "excluded", "shop", "alias", "me",
                                  "rejects", "fails", "trades", "reviews", "last_ts")}
        core.update(cat_vel={c: s["cat"][c]["vel"] for c in s["cat"]}, has_cat=sorted(cats, key=str),
                    has_sku_shard=sorted(self._sku_groups), n_cat=len(s["cat"]), n_sku=len(s["sku"]),
                    n_units=len(s["units"]), checkpoint_unix=time.time())
        _write_atomic(d / "core.json", json.dumps(core, ensure_ascii=False, separators=(",", ":")))
        _write_atomic(d / STAMP, json.dumps({"version": AGG_VERSION, "seq": s["seq"]}))
        A.touched = {"cat": set(), "sku": set()}
        self._views_full = False

    def _put(self, path, key, body):
        if self._shard_bytes.get(key) != body:          # rewrite only shards that changed since the last checkpoint
            _write_atomic(path, body)
            self._shard_bytes[key] = body


def catch_up(root=None, is_own=None, catalog=None, checkpoint=True, views=True):
    """Buyer-folder update path (no resident aggregator there). Call once at the start of a short-lived process
    such as market_buyer, after its ledger ingest:

        A, info = aggregates.catch_up(STATE, owner_checker(state=STATE), catalog)
        table = A.reputation_table(); A.category(c); A.sku(s); cards.context_card(A, ...)

    Loads the snapshot (one read), applies only the ledger events after its byte cursor, and, when anything was
    applied (or the snapshot/views are missing), writes views + snapshot under the folder lock. Returns the fresh
    in-memory Aggregates (same API as StatsView, no staleness) and timings. If another process holds the lock,
    nothing is written (info["wrote"] False) and the returned stats are still fresh."""
    t0 = time.perf_counter()
    R = Resident(root, is_own if is_own is not None else owner_checker(state=root), catalog, views=views)
    t1 = time.perf_counter()
    n = R.refresh()
    t2 = time.perf_counter()
    wrote = False
    if checkpoint and (n or not R.A.snapshot_path().exists() or (views and R._views_full)):
        wrote = R.checkpoint()
    t3 = time.perf_counter()
    R.A.touched = None
    return R.A, {"applied": n, "loaded": R.loaded, "wrote": wrote, "seq": R.A.s["seq"],
                 "startup_ms": round((t1 - t0) * 1000, 3), "refresh_ms": round((t2 - t1) * 1000, 3),
                 "checkpoint_ms": round((t3 - t2) * 1000, 3), "total_ms": round((t3 - t0) * 1000, 3)}


class StatsView(Aggregates):
    """Read-only stats for a short-lived hook process (restock, market_buyer, review): reads views/core.json
    (shops, my numbers, category velocities; size bounded by shops + categories) and then only the category
    shards it asks for. Same query API and same answers as the Resident's Aggregates at its last checkpoint;
    stale by at most CHECKPOINT_EVENTS events / CHECKPOINT_S seconds (core["seq"] says how far it is)."""

    def __init__(self, root=None, catalog=None):
        super().__init__(root, None, catalog)
        self.read_ms = 0.0                       # time spent reading shard files (for the benchmark)
        core = json.loads((self.root / VIEWS / "core.json").read_text(encoding="utf-8"))
        s = self.blank()
        s.update({k: v for k, v in core.items() if k in s})
        self.core = core
        self.s = s
        self._has = set(core["has_cat"])
        self._has_sku = set(core.get("has_sku_shard") or ())
        self._loaded = set()
        self._loaded_sku = set()

    def _read(self, sub, name):
        t = time.perf_counter()
        sh = json.loads((self.root / VIEWS / sub / name).read_text(encoding="utf-8"))
        self.read_ms += (time.perf_counter() - t) * 1000
        return sh

    def _ensure(self, cat):
        """Category-level stats only (views/cat/...): price histogram, velocity, hours-left tables."""
        if cat in self._loaded or cat not in self._has:
            return
        self._loaded.add(cat)
        sh = self._read("cat", _shard_name(cat))
        if sh["st"] is not None:
            self.s["cat"][cat] = sh["st"]
        if sh["hl"] is not None:
            self.s["hl"][cat] = sh["hl"]
        if sh["mkt_hl"] is not None:
            self.s["mkt_hl"][cat] = sh["mkt_hl"]

    def _ensure_sku(self, sku):
        """The SKU shard holding `sku` (views/sku/<sha1(sku)[:2]>.json), read only when sku() is asked."""
        name = _sku_shard(sku)
        if name in self._loaded_sku or name not in self._has_sku:
            return
        self._loaded_sku.add(name)
        self.s["sku"].update(self._read("sku", name)["sku"])

    def category(self, category, now=None):
        self._ensure(category)
        return super().category(category, now)

    def sku(self, sku, now=None):
        self._ensure_sku(sku)
        return super().sku(sku, now)

    def cat_rank(self, now=None):
        now = now or self.now()
        tau = VEL_TAU_H * 3600
        v = self.core["cat_vel"]
        return sorted(v, key=lambda c: (-round(decay_at(v[c], now, tau) / VEL_TAU_H, 6), c))

    def me(self, now=None):
        m = super().me(now)
        n = self.core["n_units"]
        per_day = decay_at(self.s["me"]["sold_vel"], now or self.now(), VEL_TAU_H * 3600) / VEL_TAU_H * 24
        m["live_units"] = n
        m["inventory_days"] = round(n / per_day, 2) if per_day >= MIN_RATE else None
        return m

    def summary(self):
        out = super().summary()
        out.update(categories=self.core["n_cat"], skus=self.core["n_sku"])
        return out

    def apply(self, ev):
        raise TypeError("StatsView is read-only; the Resident applies events")


def code_sha256(files=("aggregates.py", "cards.py")):
    h = hashlib.sha256()
    for f in files:
        p = HERE / f
        if p.exists():
            h.update(f.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, str(HERE))
    ap = argparse.ArgumentParser(description="update the aggregates snapshot from the ledger and print a summary")
    ap.add_argument("--state", default=os.environ.get("R3_STATE", ".r3"))
    ap.add_argument("--catalog", default=None)
    a = ap.parse_args()
    cat = {}
    if a.catalog:
        c = json.loads(Path(a.catalog).read_text(encoding="utf-8"))
        cat = {x["sku"]: x for x in c} if isinstance(c, list) else c
    A = Aggregates.open(a.state, owner_checker(state=a.state), cat)
    n = A.update()
    A.save()
    print(json.dumps(dict(A.summary(), applied=n), ensure_ascii=False))
