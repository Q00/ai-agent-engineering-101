"""R3 layer 3: decisions in code, driven by policy.json. No LLM here.

Hard guard: a seller price is never below the unit's cost (`floor_price` >= cost + 1, and `guard_price` is the
last check before a receipt is signed). Every price leaving this module is an int (the kit's checkout needs int).
"""
import copy
import hashlib
import json
import math
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def policy_path(p=None):
    """v1.4 (CR new issue 1): a relative R3_POLICY (or path argument) is resolved against the r3 directory, not
    the cwd. The kit runs hooks in the agent folder, so `R3_POLICY=mock/policy_warranty.json` used to fail there."""
    p = Path(p or os.environ.get("R3_POLICY") or HERE / "policy.json").expanduser()
    return p if p.is_absolute() else HERE / p


POLICY_FILE = policy_path()


class PolicyError(ValueError):
    pass


def json_int(s):
    """v1.16 (QA v1.15 minor: a 5,000-digit JSON int is over Python's int-digit limit, so json.loads raised and
    read_json saw the whole inventory.json as {} -> silent buy 0 / monitor exit 3): parse_int for json.loads that
    turns an over-limit integer into a float (inf for that size), so the one bad field gets the normal
    "not finite" handling and the rest of the file is read."""
    try:
        return int(s)
    except ValueError:
        return float(s)


def load(path=None):
    p = policy_path(path) if path else POLICY_FILE
    raw = p.read_bytes()
    pol = json.loads(raw)
    validate(pol)
    pol["_hash"] = hashlib.sha256(raw).hexdigest()[:12]
    return pol


class PolicyWatcher:
    """v1.16 (CoS-approved candidate): a long-running hook (the seller A2A server) re-reads policy.json when its
    bytes change, so a checklist edit (3-0 / 3-0b reverts) reaches it without a kit restart.
    get() -> (policy, event). At most one file read per min_interval_s (env R3_POLICY_CHECK_S, default 5 s).
      same 12-hex hash as the current policy        -> no parse, no reload, event None;
      new hash, JSON + validate() OK, top-level keys of the current policy all present -> switch, ("reload", old, new);
      new hash that fails (bad / partial JSON, not an object, validate PolicyError, missing top-level keys, read
      error)                                         -> KEEP the current policy, ("reload_failed", hash, error) once
                                                        per bad hash (the same bad bytes are not re-parsed / re-warned);
      rapid changes: each read takes the newest bytes; versions written in between are never seen (no queue)."""

    def __init__(self, path=None, pol=None, min_interval_s=None):
        self.path = policy_path(path) if path else POLICY_FILE
        self.pol = pol if pol is not None else load(path)
        self.min_interval_s = (float(os.environ.get("R3_POLICY_CHECK_S", "5")) if min_interval_s is None
                               else float(min_interval_s))
        self._t = time.monotonic()
        self._bad = None
        self.reloads = self.failures = 0

    def get(self, now=None):
        now = time.monotonic() if now is None else now
        if self.min_interval_s > 0 and now - self._t < self.min_interval_s:
            return self.pol, None
        self._t = now
        try:
            raw = self.path.read_bytes()
        except OSError as e:
            return self._fail("read", f"{type(e).__name__}: {str(e)[:150]}")
        h = hashlib.sha256(raw).hexdigest()[:12]
        if h == self.pol.get("_hash"):
            self._bad = None
            return self.pol, None
        if h == self._bad:
            return self.pol, None
        try:
            new = json.loads(raw)
            if not isinstance(new, dict):
                raise PolicyError(f"policy is a {type(new).__name__}, not an object")
            missing = sorted(k for k in self.pol if not k.startswith("_") and k not in new)
            if missing:
                raise PolicyError(f"top-level keys missing: {missing[:5]}")
            validate(new)
        except Exception as e:                           # noqa: BLE001 - never lose the running policy
            return self._fail(h, f"{type(e).__name__}: {str(e)[:150]}")
        new["_hash"] = h
        old, self.pol, self._bad = self.pol.get("_hash"), new, None
        self.reloads += 1
        return new, ("reload", old, h)

    def _fail(self, h, err):
        if h == self._bad:
            return self.pol, None
        self._bad = h
        self.failures += 1
        return self.pol, ("reload_failed", h, err)


# --- v2.12 concurrent comparison arms: policy_arms.json next to the live policy.json. Each live arm writes its value
# at its path for the share of sale requests whose salt hashes below the share (arm "b"); the rest keep the policy
# as it is (arm "a"). No file, an empty list or only expired arms -> the policy object itself, unchanged.
# The seller salts by the sold unit id and applies arms after the unit is picked: shop keys read by the pick or by
# decline_texts (c_first_leaves, food_min_left_frac, decline_texts, ...) and restock keys have no effect when armed.
ARMS_FILE = "policy_arms.json"
ARMS_MAX = 8
ARM_ROOTS = ("defaults", "categories", "shop")         # the seller reads these; other roots are ignored
_ARMS_CACHE = {}                                       # arms file path -> ((mtime_ns, size), valid arms)


def _arm_ok(a):
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if not isinstance(a, dict):
        return False
    p = a.get("path")
    return (isinstance(a.get("id"), int) and not isinstance(a.get("id"), bool)
            and isinstance(p, list) and len(p) >= 2 and all(isinstance(k, str) and k for k in p) and p[0] in ARM_ROOTS
            and "value" in a and num(a.get("share")) and 0 < a["share"] < 1 and num(a.get("until")))


def load_arms(policy_path, now):
    """v2.12: valid arms of policy_arms.json (beside `policy_path`) whose `until` (epoch s) is after `now`. Parsed
    once per (mtime, size) of the file; a stat per call. Missing / unreadable / corrupt file, not {"arms": [...]},
    or more than ARMS_MAX arms -> []; an arm with a bad field (id int, path list of >= 2 keys under ARM_ROOTS, value
    present, 0 < share < 1, until a number) or a repeated id is skipped. Never raises."""
    try:
        p = Path(policy_path).parent / ARMS_FILE
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _ARMS_CACHE.get(str(p))
        if hit is None or hit[0] != key:
            ok, seen = [], set()
            try:
                raw = json.loads(p.read_bytes())
                arms = raw.get("arms") if isinstance(raw, dict) else None
            except Exception:                            # noqa: BLE001 - a corrupt file is no arms
                arms = None
            if isinstance(arms, list) and len(arms) <= ARMS_MAX:
                for a in arms:
                    if _arm_ok(a) and a["id"] not in seen:
                        seen.add(a["id"])
                        ok.append(a)
            hit = _ARMS_CACHE[str(p)] = (key, ok)
        return [a for a in hit[1] if a["until"] > now]
    except Exception:                                    # noqa: BLE001 - never fail a request on arms
        return []


def arm_salt(unit_id):
    """v2.12: salt of one sale: the unit id. Live requests carry no buyer identity; the market settle record carries
    the unit, so the evaluator rebuilds the salt from it with this same function. A unit keeps its arm while in stock."""
    return str(unit_id)


def arm_pick(arm_id, salt, share=0.5):
    """v2.12: True = arm b. sha256 of "<arm_id>:<salt>", first 8 hex digits as a fraction of 2**32, below share."""
    return int(hashlib.sha256(f"{arm_id}:{salt}".encode()).hexdigest()[:8], 16) / 2**32 < share


def _set_path(pol, path, value):
    """Copy-on-write: a new top dict and new dicts along `path` (missing ones created); everything else shared."""
    out = dict(pol)
    cur, src = out, pol
    for k in path[:-1]:
        nxt = src.get(k) if isinstance(src, dict) else None
        if nxt is not None and not isinstance(nxt, dict):
            raise PolicyError(f"arm path {path!r}: {k!r} is not an object")
        cur[k] = dict(nxt or {})
        cur, src = cur[k], nxt
    cur[path[-1]] = copy.deepcopy(value)
    return out


def with_arms(pol, arms, salt):
    """v2.12: (pol2, picked). pol2 = pol with the value of every arm picked b for this salt at its path (pol itself
    when none is b; pol is never mutated); picked = {arm id: "a" | "b"} for every arm given. When the arms make a
    policy that fails validate (or a path runs through a non-object), pol is returned and its b arms are "invalid"."""
    picked, b = {}, []
    for a in arms or ():
        if arm_pick(a["id"], salt, a["share"]):
            b.append(a)
            picked[a["id"]] = "b"
        else:
            picked[a["id"]] = "a"
    if not b:
        return pol, picked
    try:
        pol2 = pol
        for a in b:
            pol2 = _set_path(pol2, a["path"], a["value"])
        validate(pol2)
    except Exception:                                    # noqa: BLE001 - a bad arm never replaces the policy
        picked.update({a["id"]: "invalid" for a in b})
        return pol, picked
    return pol2, picked


def _check(name, v, bounds):
    if name in bounds and isinstance(v, (int, float)) and not isinstance(v, bool):
        lo, hi = bounds[name]
        if not lo <= v <= hi:
            raise PolicyError(f"{name}={v} outside [{lo}, {hi}]")


def validate(pol):
    b = pol.get("bounds") or {}
    sp = pol.get("shop") or {}
    rp = sp.get("restock_priority")                    # v1.20a
    if rp is not None and (not isinstance(rp, list) or not all(isinstance(x, str) and x for x in rp)):
        raise PolicyError("shop.restock_priority must be a list of category names")
    f = sp.get("max_spend_frac")                        # v1.23: per-run spend as a share of the balance
    if f is not None and (isinstance(f, bool) or not isinstance(f, (int, float)) or not 0 < f <= 1):
        raise PolicyError(f"shop.max_spend_frac={f!r} outside (0, 1]")
    cap = sp.get("star_price_cap")                      # v2.1: goods grade -> first-ask cap as a share of list
    if cap is not None and (not isinstance(cap, dict) or not all(
            g in ("A", "B", "C") and not isinstance(v, bool) and isinstance(v, (int, float)) and 0.5 <= v <= 1.0
            for g, v in cap.items())):
        raise PolicyError(f"shop.star_price_cap={cap!r} must map grades A/B/C to shares in [0.5, 1]")
    mf = sp.get("market_floor_frac")                    # v2.7: A/B goods floor as a share of the SKU market price
    if mf is not None and (isinstance(mf, bool) or not isinstance(mf, (int, float)) or not 0.5 <= mf <= 1.0):
        raise PolicyError(f"shop.market_floor_frac={mf!r} outside [0.5, 1]")
    gcf = sp.get("goods_cost_frac")                     # v2.8: goods cost / list for the below-market restock check
    if gcf is not None and (isinstance(gcf, bool) or not isinstance(gcf, (int, float)) or not 0.4 <= gcf <= 0.9):
        raise PolicyError(f"shop.goods_cost_frac={gcf!r} outside [0.4, 0.9]")
    gfl = sp.get("grade_floor_list")                    # v2.11: goods floor as a share of list, per grade
    if gfl is not None and (not isinstance(gfl, dict) or any(
            g not in ("A", "B", "C") or isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.3 <= v <= 0.9
            for g, v in gfl.items())):
        raise PolicyError(f"shop.grade_floor_list={gfl!r}: grades A/B/C, shares in [0.3, 0.9]")
    cfl = sp.get("c_first_leaves")                      # v2.11: leaf categories that offer grade C first
    if cfl is not None and (not isinstance(cfl, list) or not all(isinstance(x, str) and x for x in cfl)):
        raise PolicyError(f"shop.c_first_leaves={cfl!r} must be a list of leaf names")
    fml = sp.get("food_min_left_frac")                  # v2.10: unstated-need food needs this share of shelf left
    if fml is not None and (isinstance(fml, bool) or not isinstance(fml, (int, float)) or not 0.0 <= fml <= 0.9):
        raise PolicyError(f"shop.food_min_left_frac={fml!r} outside [0, 0.9]")
    for blk in list((pol.get("categories") or {}).values()) + [sp]:
        k = blk.get("skus_per_category")
        if k is not None and (isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 5):
            raise PolicyError(f"skus_per_category={k!r} outside [1, 5]")
    bu = pol.get("buyer") or {}                         # v1.20b: attempt cap and time guard
    for k, lo, hi in (("max_shops", 1, 8), ("extra_attempt_window_s", 0, 200), ("expiry_guard_s", 0, 600)):
        v = bu.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi):
            raise PolicyError(f"buyer.{k}={v!r} outside [{lo}, {hi}]")
    if isinstance(bu.get("max_shops"), float):
        raise PolicyError("buyer.max_shops must be an int")
    for kind, blk in (bu.get("defaults") or {}).items():   # v1.22: optional per-kind buyer deadline
        v = blk.get("deadline_s") if isinstance(blk, dict) else None
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 280):
            raise PolicyError(f"buyer.defaults.{kind}.deadline_s={v!r} outside (0, 280]")
    blocks = list((pol.get("defaults") or {}).values()) + list((pol.get("categories") or {}).values())
    blocks += list((pol.get("buyer", {}).get("defaults") or {}).values()) + list((pol.get("buyer", {}).get("categories") or {}).values())
    for blk in blocks:
        for k, v in blk.items():
            _check(k, v, b)
    for kind in ("food", "goods"):
        if kind not in (pol.get("defaults") or {}):
            raise PolicyError(f"defaults.{kind} missing")
    v = pol.get("volume") or {}
    for k, lo, hi in (("relax_max_pay_frac", 0.3, 1.0), ("relax_ask_cut", 0.0, 0.5), ("pace_gain", 0.0, 10.0),
                      ("target_settled", 0, 10_000), ("margin", 0, 10_000)):
        if k in v and not lo <= v[k] <= hi:
            raise PolicyError(f"volume.{k}={v[k]} outside [{lo}, {hi}]")
    return True


def leaf(category):
    return (category or "").split(">")[-1]


def resolve(pol, category, kind, side="shop"):
    """defaults[kind] overlaid by categories[full path] or categories[leaf name]."""
    src = pol if side == "shop" else pol["buyer"]
    out = dict(src["defaults"][kind if kind in src["defaults"] else "goods"])
    cats = src.get("categories") or {}
    for key in (leaf(category), category):
        if key in cats:
            out.update(cats[key])
    return out


def as_int_price(x):
    """Whole credits only. 120.0 -> 120; 120.4 -> ceil for a seller ask is done by callers explicitly."""
    if isinstance(x, bool) or x is None:
        raise TypeError("price is not a number")
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    raise TypeError(f"price {x!r} is not a whole number")


# --- shop side

FOOD_GRADE_CUTS = (0.8, 0.45)        # the market's: share of shelf life left for food grade A, B


def liquidating(inv, pol):
    """Near the bankruptcy line: stop buying and sell at cost + 1 to turn stock back into balance."""
    bal = (inv or {}).get("balance")
    return bal is not None and bal < pol["shop"]["liquidation_line"]


def volume_relax(settled, pol, now=None):
    """Min-volume guard: relax level r in [0, 1] for one side (shop or buyer) from its settled Round 3 count.
    goal = target_settled + margin. r = 0 once settled >= goal. Before that r grows with how far the count is
    behind a straight-line pace to the goal at round end: r = clamp(pace_gain * (expected - settled) / goal),
    expected = goal * elapsed share of the round. Inside the last late_hours with settled < target: r = 1.
    Pure: same inputs, same r."""
    v = pol.get("volume") or {}
    target = int(v.get("target_settled", 20))
    goal = target + int(v.get("margin", 0))
    if settled >= goal:
        return 0.0
    now = time.time() if now is None else now
    t0, t1 = v.get("round_start_ts"), v.get("round_end_ts")
    if not (t0 and t1 and t1 > t0):
        return 0.0
    if now < t0:
        return 0.0
    if settled < target and t1 - now <= v.get("late_hours", 48) * 3600:
        return 1.0
    frac = min(1.0, (now - t0) / (t1 - t0))
    r = v.get("pace_gain", 2.0) * (goal * frac - settled) / goal
    return round(max(0.0, min(1.0, r)), 4)


def floor_price(unit, pol, inv=None, now=None, relax=0.0):
    """Lowest price the shop accepts: cost * (1 + min_margin * (1 - relax)); 0 margin when liquidating or when
    food is near its date (liquidate_hours). Never below cost + 1 (hard guard), whatever relax is."""
    c = resolve(pol, unit.get("category"), unit.get("kind"))
    cost = int(unit["cost"])
    margin = c["min_margin"] * (1 - max(0.0, min(1.0, relax)))
    if liquidating(inv, pol):
        margin = pol["shop"].get("liquidation_margin", 0.0)
    h = hours_left(unit, now)
    if unit.get("kind") == "food" and h is not None and h < c.get("liquidate_hours", 0):
        margin = 0.0
    return max(cost + 1, math.ceil(cost * (1 + margin)))


def endgame_floor(unit, pol, inv=None, now=None, relax=0.0):
    """v1.5/v1.6: the lowest price the seller can accept from `endgame_round` on = cost x (1 + endgame_margin),
    never above the normal floor and never at or below cost (guard_price still checks cost + 1 at signing).
    v1.6 (CR v1.5 major 3): respond() accepts an offer between this and the normal floor ONLY when the buyer's offer
    has stalled (offer <= its previous offer), its raise shrank (a buyer at its cap, who would otherwise walk), or the
    seller would otherwise reject (past max_rounds and the buyer no longer rising, or patience_rounds used up). A buyer
    still raising at its usual pace is countered at the normal floor, so it pays the floor if its cap allows (v1.5
    accepted at round 4 at once, so some buyers who would have paid the floor got the thinner margin: seed 11
    G315bc02e 13,856 -> 11,882). None when the policy has no endgame_margin."""
    c = resolve(pol, unit.get("category"), unit.get("kind"))
    m = c.get("endgame_margin")
    if m is None:
        return None
    cost = int(unit["cost"])
    return min(floor_price(unit, pol, inv, now, relax), max(cost + 1, math.ceil(cost * (1 + max(0.0, float(m))))))


def market_floor(unit, pol, stats, ask=None):
    """v2.7: lowest price for an A/B goods unit = shop.market_floor_frac x the SKU's market settle price
    (shop.ask_quantile, >= 5 settles), never above the ask. None when the key is unset, for food and grade C, or
    when the SKU has too few settles. 24 h to 11-07 20:00 KST: 103 of 326 goods sales went below 0.9 x the same
    SKU and grade's market median, 66 of them after 3+ rounds (the endgame took the cost floor); the gap to 0.9 x
    market was 4.9M. One curtain SKU: asks 0.75-0.76 of list = market 0.75, sold 0.55-0.66 after 4-5 rounds.
    v2.11 (11-10, book): with shop.grade_floor_list[grade] set, a goods unit of that grade floors at that share of its
    list price instead (never above the ask). Our grade C sofas and beds sold at 0.43-0.47 of list while the market
    settled grade C at 0.56-0.57; 118 C furniture units (71M at cost) sat unsold."""
    sp = pol.get("shop") or {}
    gf = (sp.get("grade_floor_list") or {}).get(unit.get("grade"))
    if gf and unit.get("kind") != "food" and unit.get("list_price"):
        m = int(math.ceil(int(unit["list_price"]) * float(gf)))
        return m if ask is None else min(m, int(ask))
    frac = sp.get("market_floor_frac")
    if not frac or unit.get("kind") == "food" or unit.get("grade") not in ("A", "B"):
        return None
    s = _stat(stats, "sku", unit.get("sku"))
    q = sp.get("ask_quantile") or "p75"
    if not s or s.get("n", 0) < 5 or not s.get(q):
        return None
    m = int(math.ceil(s[q] * float(frac)))
    return m if ask is None else min(m, int(ask))


def food_grade_now(unit, catalog_shelf_hours=None, now=None, safety=0.0):
    """Food grade at this moment from shelf life left (A >= 80%, B >= 45%). With `safety`, a unit within that
    share of a cut is graded down. None when it cannot be known (no shelf_hours in the catalog or the unit, or no
    expires_at): the supply grade is stale for food, so callers must not claim it (v1.2, QA P6)."""
    if unit.get("kind") != "food":
        return unit.get("grade")
    h = hours_left(unit, now)
    sh = catalog_shelf_hours or unit.get("shelf_hours")
    if h is not None and h <= 0:
        return "expired"
    if h is None or not sh:
        return None
    if h <= 0:
        return "expired"
    left = h / sh - safety
    return "A" if left >= FOOD_GRADE_CUTS[0] else "B" if left >= FOOD_GRADE_CUTS[1] else "C"


def sellable(unit, pol, now=None):
    """False for food so near its date that it would expire before a buyer can use it (dispose_hours):
    left to be thrown away at cost by the market rather than sold with a false promise."""
    if unit.get("grade") == "expired":
        return False
    c = resolve(pol, unit.get("category"), unit.get("kind"))
    h = hours_left(unit, now)
    return not (unit.get("kind") == "food" and h is not None and h < c.get("dispose_hours", 0))


def guard_price(unit, price):
    """Last check before signing. Raises unless the price is an int strictly above cost (same bar as floor_price)."""
    price = as_int_price(price)
    if price <= int(unit["cost"]):
        raise PolicyError(f"price {price} not above cost {unit['cost']}")
    return price


def hours_left(unit, now=None):
    e = unit.get("expires_at")
    return None if not e else (e - (now or time.time())) / 3600


def ask_price(unit, pol, stats=None, now=None, inv=None, shelf_hours=None, relax=0.0):
    """First ask: list_price * price_mult, less a discount for the grade the unit has NOW (food decays), less
    liquidate_discount for food near its date; moved down to the market's p75 settle price when stats know it.
    v1.18: shop.ask_quantile (e.g. "p50") replaces p75 when the SKU itself has >= 5 settles; the category fallback
    stays p75 (a category median mixes SKUs priced 0.5-1.5x apart). Never below floor_price.
    v2.1: shop.star_price_cap {grade: share} caps a goods ask at share x list (our goods A reviews 24 h to 11-05
    13:30 KST: 4.49 at 0.77-0.78 of list, 3.95 at 0.78-0.79, 3.65 at 0.80-0.81)."""
    c = resolve(pol, unit.get("category"), unit.get("kind"))
    lp = int(unit.get("list_price") or unit["cost"] * 2)
    g = food_grade_now(unit, shelf_hours, now)
    if unit.get("kind") == "food" and g not in ("A", "B", "C"):
        g = "C"                                          # unknown freshness: priced as the worst grade
    p = lp * c["price_mult"] * (1 - (c.get("fresh_discount") or {}).get(g, 0))
    h = hours_left(unit, now)
    if unit.get("kind") == "food" and h is not None and h < c.get("liquidate_hours", 0):
        p *= 1 - c.get("liquidate_discount", 0)
    ss = _stat(stats, "sku", unit.get("sku"))
    s = ss or _stat(stats, "category", unit.get("category"))
    q = (pol["shop"].get("ask_quantile") or "p75") if ss else "p75"
    if s and s.get("n", 0) >= 5 and s.get(q):
        p = min(p, s[q] * 1.0)
    p *= 1 - (pol.get("volume") or {}).get("relax_ask_cut", 0.0) * max(0.0, min(1.0, relax))
    cap = (pol["shop"].get("star_price_cap") or {}).get(g) if unit.get("kind") != "food" else None
    if cap:
        p = min(p, lp * cap)
    return max(floor_price(unit, pol, inv, now, relax), int(round(p)))


def respond(unit, pol, ask, offer, rounds, inv=None, now=None, relax=0.0, prev_offer=None, history=None,
            market=None):
    """Seller move on a buyer's offer: ("accept", price) | ("counter", price) | ("reject", None).
    relax (min-volume guard) lowers the floor margin and doubles the concession at r = 1; never below cost.
    `history`: the buyer's earlier offers in this negotiation, oldest first (prev_offer = history[-1] if not given).
    Endgame (v1.6, narrowed from v1.5): from endgame_round an offer in [endgame_floor, floor) is accepted only when
      (a) the buyer has stalled: offer <= its previous offer, or
      (b) the buyer would otherwise walk: its raise shrank (offer - prev < prev - prev2), the sign of a buyer that
          has hit its cap (a capped linear ramp; our own buyer walks instead of repeating its cap), or
      (c) the seller would otherwise reject (past max_rounds, not rising or patience_rounds used up).
    A buyer still raising at its usual pace is countered at the normal floor (past max_rounds up to patience_rounds),
    so a buyer whose cap reaches the floor pays the floor.
    `market` (v2.7, market_floor): when set, neither the floor nor the endgame floor goes below it (not when
    liquidating or relaxing for volume)."""
    c = resolve(pol, unit.get("category"), unit.get("kind"))
    floor = floor_price(unit, pol, inv, now, relax)
    if market is not None and relax <= 0 and not liquidating(inv, pol):
        floor = max(floor, int(market))
    hist = [int(x) for x in (history or []) if x is not None]
    if prev_offer is None and hist:
        prev_offer = hist[-1]
    if offer is not None and offer >= ask:
        return "accept", guard_price(unit, int(offer))
    nxt = max(floor, int(math.ceil(ask * (1 - c["concession"] * (1 + max(0.0, min(1.0, relax)))))))
    if offer is not None and offer >= nxt:
        return "accept", guard_price(unit, int(offer))
    hard = endgame_floor(unit, pol, inv, now, relax)
    if hard is not None and hard < floor and market is not None and relax <= 0 and not liquidating(inv, pol):
        hard = max(hard, int(market))                   # v2.7: the endgame stops at the market floor too
    late = hard is not None and offer is not None and offer >= hard and rounds >= c.get("endgame_round", c["max_rounds"])
    stalled = offer is not None and prev_offer is not None and offer <= prev_offer
    shrank = offer is not None and len(hist) >= 2 and prev_offer == hist[-1] and offer - hist[-1] < hist[-1] - hist[-2]
    if late and (stalled or shrank):
        return "accept", guard_price(unit, int(offer))  # v1.6 (a)/(b): the buyer stopped raising or is at its cap
    if rounds >= c["max_rounds"]:
        if offer is not None and offer >= floor:
            return "accept", guard_price(unit, int(offer))
        if ask > floor:
            return "counter", floor
        rising = offer is not None and prev_offer is not None and offer > prev_offer
        if rising and rounds < c.get("patience_rounds", 0):
            return "counter", floor                 # v1.5: buyer still raising toward the floor: keep talking
        if late:
            return "accept", guard_price(unit, int(offer))  # v1.6 (c): the alternative is a reject
        return "reject", None
    return "counter", guard_price(unit, nxt)


def warranty_hours(unit, pol, stats=None, price=None):
    """Goods: offer the warranty only when the expected refund cost is below the expected sales uplift.
    refund cost = fail_rate(category, from stats; else prior_fail_rate) * price; uplift = warranty_uplift * price
    (buyers whose accept asks for warranty_hours refuse units without one). Hours = policy hours per grade,
    capped by max_warranty_hours (goods break within 6 h)."""
    if unit.get("kind") != "goods":
        return 0
    c = resolve(pol, unit.get("category"), "goods")
    hours = int((c.get("warranty_hours") or {}).get(unit.get("grade"), 0))
    if hours <= 0:
        return 0
    s = _stat(stats, "category", unit.get("category"))
    fail = s["fail_rate"] if s and s.get("n", 0) >= 10 and s.get("fail_rate") is not None else c.get("prior_fail_rate", 0.05)
    price = price or unit.get("list_price") or 0
    if fail * price >= c.get("warranty_uplift", 0) * price:
        return 0
    return min(hours, int(c.get("max_warranty_hours", 6)))


def receipt_terms(unit, pol, stats=None, price=None, now=None, shelf_hours=None):
    """unit, honest claim, market certificate shown, warranty per warranty_hours.
    Food: claim = grade now (checkout and settle follow the receipt within ~0.1 s; the market counts the claim true
    if it matches either), omitted when the grade at now + claim_window_s differs (a cut is that close) or the grade
    cannot be known (no shelf_hours). Never an over- or under-claim."""
    c = resolve(pol, unit.get("category"), unit.get("kind"))
    now = time.time() if now is None else now
    if unit.get("kind") == "food":
        g = food_grade_now(unit, shelf_hours, now)
        if g != food_grade_now(unit, shelf_hours, now + c.get("claim_window_s", 10)):
            g = None
    else:
        g = unit.get("grade")
    t = {"unit": unit["unit"], "claim": g if g in ("A", "B", "C") else None, "cert": True}
    w = warranty_hours(unit, pol, stats, price)
    if w > 0:
        t["warranty_hours"] = w
    return {k: v for k, v in t.items() if v is not None}


def idle_pause_s(pol):
    """v1.7 (c): seconds without a market settle after which restock pauses. R3_IDLE_PAUSE_S overrides the policy
    value (mock runs only: the mock compresses a round into ~1 minute; logged by the restock hook)."""
    v = os.environ.get("R3_IDLE_PAUSE_S")
    try:
        return float(v) if v not in (None, "") else float(pol["shop"].get("idle_pause_s") or 0)
    except ValueError:
        return float(pol["shop"].get("idle_pause_s") or 0)


def _resume(act, now=None):
    """v1.10: manual resume time, never later than now (the restock hook also keeps the first-seen time of a future
    mtime, so a mistaken future `touch` cannot hold the gate open)."""
    r = (act or {}).get("resume_ts")
    if not isinstance(r, (int, float)):
        return None
    return min(r, now) if now is not None else r


def demand_ref(act, now=None):
    """v1.9 (c): (timestamp, source) of the newest demand evidence for the idle gate.
    market_demand: newest market order/result (any buyer, any shop: GET /api/ledger), market settle (any shop) or a
    manual resume (<R3_STATE>/restock_resume mtime) - orders keep coming while buyers exist, whether or not we
    have stock, so this tells 'no demand' from 'no stock'. Without any order/result seen (no R3_MARKET, cursor
    lost): v1.7's newest settle or our first stock ('settle' / 'first_stock'), and the low-stock exemption applies.
    v1.10: a buyer asking our shop for stock we lack (seller `no_stock`, 'own_ask') also counts in that branch.
    v1.10: in a live market other shops' orders keep this fresh almost always (QA review_r3_01ac_v19 major 1), so
    it cannot tell 'nobody buys from US'; see own_demand_ref / idle_ref_scope. resume_ts is clamped to now.
    v1.11: our seller's accepts ('own_accept') also count in the no-signal branch."""
    act = act or {}
    res = _resume(act, now)
    if act.get("orders") or act.get("results"):
        ts = [act.get(k) for k in ("last_order_ts", "last_result_ts", "last_settle_ts")] + [res]
        ts = [t for t in ts if isinstance(t, (int, float))]
        return (max(ts), "market_demand") if ts else (None, None)
    cand = [(act.get("last_settle_ts"), "settle"), (res, "resume"), (act.get("last_no_stock_ts"), "own_ask"),
            (act.get("last_own_accept_ts"), "own_accept")]
    cand = [c for c in cand if isinstance(c[0], (int, float))]
    if cand:
        return max(cand)
    if act.get("first_stock_ts"):
        return act["first_stock_ts"], "first_stock"
    return None, None


def own_demand_ref(act, now=None):
    """v1.10 (QA review_r3_01ac_v19 major 1): (timestamp, source) of the newest demand evidence AT OUR SHOP: our own
    settle (restock.market_activity, owner.is_own_shop_ref), a buyer asking our shop for stock we lack (seller
    `no_stock` row), or a manual resume; before our first sale, our first stock ('first_stock'). Other shops'
    orders and settles do not count.
    v1.11 (QA review_r3_01ac_v110 major 1): + our seller hook's accepts ('own_accept', restock.market_activity), which
    do not depend on the ledger's settle shop format or the URL cache (a changed tunnel URL); first_stock only
    while neither an own settle nor an own accept was seen."""
    act = act or {}
    cand = [(act.get("last_own_settle_ts"), "own_settle"), (act.get("last_own_accept_ts"), "own_accept"),
            (act.get("last_no_stock_ts"), "own_ask"), (_resume(act, now), "resume")]
    if not any(isinstance(act.get(k), (int, float)) for k in ("last_own_settle_ts", "last_own_accept_ts")):
        cand.append((act.get("first_stock_ts"), "first_stock"))
    cand = [c for c in cand if isinstance(c[0], (int, float))]
    return max(cand) if cand else (None, None)


def sellable_units(inv):
    """Units we could sell now (not expired)."""
    return sum(1 for u in (inv or {}).get("units") or [] if u.get("grade") != "expired")


def idle_ref_scope(pol):
    """v1.10: 'own' or 'market' (shop.idle_ref_scope, default 'market'). R3_IDLE_REF_SCOPE overrides (mock A/B only)."""
    v = (os.environ.get("R3_IDLE_REF_SCOPE") or pol["shop"].get("idle_ref_scope") or "market").strip().lower()
    return v if v in ("own", "market") else "market"


def low_stock(inv, pol):
    """v1.10 (QA review_r3_01ac_v19 minor 2): the shop counts as (nearly) sold out for the idle gate when its
    sellable units <= idle_low_stock_units OR its sellable stock at cost <= idle_low_stock_frac x the (a) inventory
    cap (max_inventory_cost_frac x balance). v1.9 used units only, so one unit nobody wants re-created the v1.7
    deadlock when no order signal was seen. -> (low, units, sellable cost)."""
    sp = pol["shop"]
    units = sellable_units(inv)
    cost = inventory_cost(inv)[0]
    lim = float(sp.get("idle_low_stock_frac") or 0) * float(sp.get("max_inventory_cost_frac") or 0) * \
        int((inv or {}).get("balance") or 0)
    return units <= int(sp.get("idle_low_stock_units", 0) or 0) or (lim > 0 and cost <= lim), units, cost


def own_velocity(activity, catalog, pol, now=None):
    """v1.9 (b): our own shop's sales per hour by category, same estimator as aggregates (exponentially decayed
    settle count, tau own_velocity_tau_h, / tau). aggregates' velocity_per_h leaves our shop out (mine_shop), so
    restock_plan adds this to it: our sales are demand too (a buyer chose our unit). {} when off or no data."""
    sp = pol["shop"]
    if not sp.get("velocity_include_own"):
        return {}
    tau = float(sp.get("own_velocity_tau_h") or 24.0)
    now = now or time.time()
    out = {}
    for sku, (v, ref) in ((activity or {}).get("own_sales") or {}).items():
        cat = (catalog.get(sku) or {}).get("category")
        if cat is None or not isinstance(ref, (int, float)):
            continue
        out[cat] = out.get(cat, 0.0) + float(v) * math.exp(-max(0.0, now - ref) / (tau * 3600)) / tau
    return out


ASKED_TAU_S = 3600.0                                     # v2.13: hooks/restock.py decays own_asked with the same tau
ASKED_MIN = 2.0                                          # decayed refusals in about the last hour to count as asked


def own_asked(activity, now=None):
    """v2.13 (2026-11-11): SKUs buyers asked OUR shop for while we held none, {sku: decayed refusals} at now
    (activity own_asked, tau 1 h), only those at ASKED_MIN or more. shop.restock_asked_first false turns the use off
    in restock_plan. 13:34 labpc: 61 no_stock refusals in 1 h, 26 for one water SKU with 1 unit on hand, while the
    4 h of restock bought three other drinks SKUs (the top market settles) and none of it."""
    now = now or time.time()
    out = {}
    for sku, pair in ((activity or {}).get("own_asked") or {}).items():
        try:
            v, ref = pair
            v = float(v) * math.exp(-max(0.0, now - float(ref)) / ASKED_TAU_S)
        except (TypeError, ValueError):
            continue
        if v >= ASKED_MIN:
            out[sku] = v
    return out


def restock_gate(inv, pol, activity=None, now=None):
    """v1.7 inventory risk gate, before any buying. -> (why, info) with why None when restock may buy.
    (c) idle: no demand evidence for idle_pause_s -> pause (a quiet market would leave a full refill unsold: v1.6
        mock runs ended with 23-36 unsold units). v1.9: demand evidence = demand_ref (market orders/results/settles
        of any shop + manual resume), so a sold-out shop in a market that still has orders keeps restocking (v1.7
        paused on 'no settle' and deadlocked once sold out). With no order signal at all, the v1.7 settle/first
        stock reference is used and the gate does not pause while low_stock (v1.10: units OR cost). A shop
        that never held stock (cold start, nothing seen) is not paused.
        v1.10 idle_ref_scope "own" (QA review_r3_01ac_v19 major 1): while we hold more than low_stock, the reference
        is own_demand_ref (our settles / no_stock asks / resume; our first stock before the first sale), so
        'nobody bought from US for idle_pause_s' pauses even when other shops keep selling. When low_stock, the
        market rule above decides (a sold-out shop has no own settles; using them would re-create the v1.7
        deadlock). "market" = v1.9 behaviour.
    (d) round end: within restock_stop_before_end_h of the round end (policy volume.round_end_ts or
        R3_ROUND_END_TS) -> stop; unsold stock is a loss at the end (agent.md profit = balance - start).
    (e) v1.18 supply windows (agent.md at the Round 3 start: supply opens every 20 min for 3 min, the first window
        10 min; outside one the market answers 429 and the kit tries a line 3 times at most 30 s apart, then skips
        it): plan only while inventory.json `restock` is open or opens within restock_window_lead_s, and say in
        info["lines_fit"] how many lines fit before closes_at at restock_line_s per line (the market takes one
        supply about every 10 s)."""
    sp = pol["shop"]
    now = now or time.time()
    act = activity or {}
    win = {}
    w = (inv or {}).get("restock") or {}
    try:
        opens, closes = float(w.get("opens_at") or 0), float(w.get("closes_at") or 0)
    except (TypeError, ValueError):
        opens = closes = 0
    if opens:
        if not w.get("open") and opens - now > float(sp.get("restock_window_lead_s", 60)):
            return "window_closed", {"opens_in_s": round(opens - now)}
        if closes:
            fit = int((closes - max(now, opens)) // float(sp.get("restock_line_s", 10)))
            if fit < 1:
                return "window_closing", {"closes_in_s": round(closes - now)}
            win = {"lines_fit": fit}
    pause = idle_pause_s(pol)
    if pause > 0:
        low, units, held = low_stock(inv, pol)
        if idle_ref_scope(pol) == "own" and not low:
            oref, osrc = own_demand_ref(act, now)
            if oref and now - oref > pause:
                return "idle", {"idle_s": round(now - oref, 1), "idle_pause_s": pause, "idle_ref": osrc,
                                "idle_scope": "own", "units": units, "held_cost": held}
        ref, src = demand_ref(act, now)
        if ref and now - ref > pause:
            info = {"idle_s": round(now - ref, 1), "idle_pause_s": pause, "idle_ref": src}
            if src != "market_demand" and low:
                # v1.9 (c): no demand signal (no market order/result seen) and we are sold out / low: no settles may just
                # mean nothing to sell, so the idle gate does not hold (v1.7 deadlock: QA review_r3_01ac_v17 (c)).
                info.update(units=units, idle_low_stock_units=int(sp.get("idle_low_stock_units", 0) or 0))
            else:
                return "idle", dict(info, units=units)
    stop_h = sp.get("restock_stop_before_end_h")
    try:
        end = float(os.environ.get("R3_ROUND_END_TS") or (pol.get("volume") or {}).get("round_end_ts") or 0)
    except ValueError:
        end = 0
    if stop_h and end and now > end - float(stop_h) * 3600:
        return "round_end", {"hours_left": round((end - now) / 3600, 1)}
    return None, win


def inventory_cost(inv):
    """Total cost of the units in inventory.json (expired excluded): (all, food, goods)."""
    tot = {"food": 0, "goods": 0}
    for u in (inv or {}).get("units") or []:
        if u.get("grade") == "expired":
            continue
        tot["food" if u.get("kind") == "food" else "goods"] += int(u.get("cost") or 0)
    return tot["food"] + tot["goods"], tot["food"], tot["goods"]


def food_cap(cat, sku_row, c, sp, stats, own_v=0.0):
    """v1.7 (b): most food units of a category to hold = expected sales during the SKU's shelf life
    (category velocity_per_h x shelf_hours x food_sell_through), at least food_min_units; with no demand data
    (cold start) food_cold_units. None for no cap."""
    frac = sp.get("food_sell_through")
    if frac is None:
        return None
    s = _stat(stats, "category", cat)
    v = ((s or {}).get("velocity_per_h") or 0) + (own_v or 0)    # v1.9 (b): + our own sales
    if not v:
        return int(sp.get("food_cold_units", c.get("breadth_qty") or 1))
    exp = v * float(sku_row.get("shelf_hours") or 0) * float(frac)
    return max(int(sp.get("food_min_units", 1)), int(math.floor(exp)))


STORAGE_RATE_MAX = 0.1   # v1.14: a per-day fraction above 10 %/day is read as a percent-unit value (1 = "1 %"?)


def _short(v, n=40):
    """v1.15: repr for warnings, cut (a 401-digit value must not flood stderr / metrics)."""
    try:
        r = repr(v)
    except Exception:                                    # noqa: BLE001
        r = f"<{type(v).__name__}>"
    return r if len(r) <= n else f"{r[:n]}...({len(r)} chars)"


def storage_rate(v):
    """v1.14 (QA review_r3_01ac_v113 condition 1): inventory.json storage_per_day -> (rate, warning).
    The real-market format is unconfirmed (SCHEMA: 1 %/day = 0.01). A value that is not clearly a per-day fraction
    turns the goods cap OFF with a warning instead of crashing restock_plan or collapsing the depth:
    missing / None / 0 -> (0.0, None) = no fee, no cap (v1.12 behaviour, silent);
    bool, non-numeric string ("1%", "abc"), NaN / inf, negative -> (None, warning);
    a number above STORAGE_RATE_MAX (e.g. 1 meaning "1 %") -> (None, warning: ambiguous unit).
    Numeric strings ("0.01") are accepted as numbers."""
    if v is None:
        return 0.0, None
    if isinstance(v, bool):
        return None, f"storage_per_day {_short(v)} is a bool"
    if isinstance(v, str):
        t = v.strip()
        if t.endswith("%"):
            return None, f"storage_per_day {_short(v)} is a percent string (unit unconfirmed)"
        try:
            x = float(t)
        except (ValueError, OverflowError):
            return None, f"storage_per_day {_short(v)} is not a number"
    elif isinstance(v, (int, float)):
        try:
            x = float(v)
        except (OverflowError, ValueError):              # v1.15 (QA v1.14 major 2): a 401-digit JSON int
            return None, f"storage_per_day {_short(v)} does not fit a float"
    else:
        return None, f"storage_per_day {_short(v)} has type {type(v).__name__}"
    if not math.isfinite(x):
        return None, f"storage_per_day {_short(v)} is not finite"
    if x < 0:
        return None, f"storage_per_day {_short(v)} is negative"
    if x > STORAGE_RATE_MAX:
        return None, f"storage_per_day {_short(v)} > {STORAGE_RATE_MAX}: ambiguous unit (percent?)"
    return x, None


def goods_cap(cat, c, sp, stats, own_v=0.0, storage_per_day=0.0, warn=None):
    """v1.13 (M13): most goods units of a category to hold, from the storage fee (agent.md 140: goods in stock cost
    storage_per_day = 1 % of their cost per day). goods_max_storage_frac = the share of a unit's cost we accept to
    pay in storage while it waits; at storage_per_day that is a holding horizon of frac / storage_per_day days, so
    the cap = expected sales in that horizon = (category velocity_per_h + own) x 24 x days, at least
    goods_min_units; with no demand data goods_cold_units. None (no cap) when the key is unset or the market
    charges no storage (inventory.json storage_per_day 0 / missing).
    v1.15: a cap that is not a finite number (tiny rate -> inf) is also no cap + warning, never an exception.
    v1.14: storage_per_day is parsed by storage_rate; an unparsable / non-finite / percent-like value (and a frac that
    is not a positive number) gives None (no cap) and appends a warning to `warn` (restock_plan -> info["warnings"]).
    Break-even note: a unit is worth holding while its margin (~25-45 % of cost at list 1/0.55-0.75 cost) exceeds
    the fee, i.e. for 25-45 days; the cap is a risk bound (stock that stops selling keeps paying), not break-even."""
    frac = sp.get("goods_max_storage_frac")
    if frac is None:                                     # key unset: no cap, storage_per_day never read (v1.12)
        return None
    rate, w = storage_rate(storage_per_day)
    if w is None and (isinstance(frac, bool) or not isinstance(frac, (int, float)) or not math.isfinite(frac)
                      or frac <= 0):
        w = f"goods_max_storage_frac {frac!r} not a positive number"
    if w is not None:
        if warn is not None:
            warn.append(f"goods storage cap off: {w}")
        return None
    if rate <= 0:
        return None
    s = _stat(stats, "category", cat)
    v = ((s or {}).get("velocity_per_h") or 0) + (own_v or 0)
    if not v:
        return int(sp.get("goods_cold_units", c.get("stock_target") or 1))
    try:                                                 # v1.15 (QA v1.14 major 2): 1e-310 / 5e-324 -> floor(inf)
        x = float(v) * 24 * (float(frac) / rate)
        if not math.isfinite(x):
            raise OverflowError(f"cap {x!r} not finite")
        return max(int(sp.get("goods_min_units", 1)), int(math.floor(x)))
    except (OverflowError, ValueError, TypeError, ZeroDivisionError) as e:
        if warn is not None:
            warn.append(f"goods storage cap off: cap from storage_per_day {_short(storage_per_day)} is not finite ({e})")
        return None


def below_market(sku, x, stats, cost_frac, margin):
    """v1.18: True when the market's median settle of this SKU (>= 5 settles) is under our expected unit cost
    (list_price x cost_frac) plus margin, i.e. we could not sell it at the market price without going under floor."""
    s = _stat(stats, "sku", sku) or {}
    return bool(s.get("n", 0) >= 5 and s.get("p50") and s["p50"] < x["list_price"] * cost_frac * (1 + margin))


def expected_cost_frac(units, sp):
    """v1.18: unit cost / list_price we expect from the next supply = p75 of our units' ratios (the grade, and with
    it the cost, is drawn at supply: measured 2026-11-03 goods A 0.64, B 0.55, C 0.37), est_cost_frac with < 4 units."""
    fr = sorted(u["cost"] / u["list_price"] for u in units if u.get("cost") and u.get("list_price"))
    return fr[(3 * len(fr)) // 4] if len(fr) >= 4 else float(sp.get("est_cost_frac") or 0.75)


def level_fill(qty, held):
    """v1.30 (2026-11-05): split `qty` units over a category's top-k SKUs (rank order; held = units on hand per
    SKU) so the SKUs end as level as possible: each unit goes to the SKU holding the fewest, ties to the higher
    rank. With nothing on hand this is the old even split (qty // k, the first qty % k SKUs one more). Before v1.30
    every SKU got that even split whatever it held, so a slow SKU piled up and took the category's stock_target
    from fast ones: at 05:50 labpc held 10 sofa G5e3784e6 (57만, median 138 min from supply to sale, 18 of 28
    supplied units sold in 6 h) and no G8ec51935 (232만, 45 of 48 sold, 6.4 min) or G00327e80 (225만, 26 of 29,
    12.6 min), so the sofa need of 6 went 2/2/2 instead of 3/3/0."""
    out, lvl = [0] * len(held), list(held)
    for _ in range(qty):
        i = min(range(len(lvl)), key=lambda j: (lvl[j], j))
        out[i] += 1
        lvl[i] += 1
    return out


GOODS_COST_FRAC = 0.59   # agent.md goods grade mix (A 70%, B 20%, C 10%) x mid supply cost (65%, 50%, 35% of list)
FOOD_COST_FRAC = 0.50    # agent.md food grade mix (A 25%, B 50%, C 25%) x the same mid costs


def below_market_cost_frac(cost_frac, kind, goods_cf=None):
    """v2.8 (2026-11-08): expected cost / list for the below-market restock check. Goods use shop.goods_cost_frac when
    set, food and an unset key keep cost_frac (p75 of our units). The p75 of the units on hand runs high because the
    units that cost the most sell last: 10:40 labpc cost_frac 0.69-0.70 while our furniture supplied in 48 h cost
    A 0.66 (69% of units), B 0.52, C 0.33 of list, a mean of 0.60. At 0.69 + 5% margin every sofa whose market
    median is 0.67-0.68 of list was skipped: 4 sofas and 1 bed never stocked, among them a 3.82M sofa that settled
    680 times in 24 h at 47 shops with an expected margin of 29만 a unit (our top sofa 30만)."""
    return float(goods_cf) if goods_cf and kind != "food" else cost_frac


def surge_cost_frac(cost_frac, kind, factor):
    """v2.0b (2026-11-05): expected cost / list of a surged SKU for the below-market check = the larger of our units'
    cost_frac and the grade-mix mean x factor. Our units' costs already carry the surges they were bought under
    (10:05 labpc: cost_frac 0.70 with G1515d951 at surge 1.18), so cost_frac x factor counted the surge twice and
    skipped G1515d951 (11% of our 6 h revenue) at a p50 of 0.80 x list."""
    base = FOOD_COST_FRAC if kind == "food" else GOODS_COST_FRAC
    return max(cost_frac, base * factor) if factor > 1 else cost_frac


def surge_factors(inv):
    """v2.0a (Patch 3.2): inventory.json 'surge' = [{sku, name, units, factor}]: past 50 units/h market-wide a SKU
    costs +0.2% per unit more to restock, up to +50%. -> {sku: factor} for factors above 1, capped at 1.5."""
    out = {}
    for s in (inv or {}).get("surge") or []:
        f = s.get("factor") if isinstance(s, dict) else None
        if isinstance(f, (int, float)) and not isinstance(f, bool) and f > 1 and s.get("sku"):
            out[s["sku"]] = min(float(f), 1.5)
    return out


def margin_rate(sku, x, stats, factor=1.0):
    """v2.0a (Patch 3.3: book = cash + unsold stock at cost, so a goods SKU earns its margin, not its price): won per
    hour the market's flow of a SKU leaves over the expected supply cost = velocity_per_h (24 h decayed, other shops)
    x max(0, p50 - 0.59 x surge factor x list). 0 when the stats have fewer than 3 settles, no p50 or no velocity."""
    s = _stat(stats, "sku", sku) or {}
    v, p50 = s.get("velocity_per_h") or 0, s.get("p50")
    if not v or not p50 or (s.get("n") or 0) < 3:
        return 0.0
    return float(v) * max(0.0, float(p50) - GOODS_COST_FRAC * factor * float(x["list_price"]))


def restock_plan(inv, demand, catalog, pol, stats=None, rng=None, activity=None, now=None, taste=None):
    """Buy list for the restock hook: categories ranked by stats (velocity) when known, else by spread; keep
    stock_target units per category; never spend below balance_floor nor beyond max_spend_per_run.
    v1.7: restock_gate (idle / round end), inventory cost cap (max_inventory_cost_frac x balance) and food depth
    capped by expected sell-through (food_cap). v1.13: goods depth capped by the storage fee (goods_cap).
    v1.19: taste = {category: next-hour probability} from an adopted taste gate (hooks/restock.py) ranks the
    categories it covers first, by probability; it changes the ranking only (targets, caps, budget unchanged).
    v1.20a: shop.restock_priority (category full paths or leaf names) ranks those categories before taste and
    velocity; categories[cat].skus_per_category spreads a category over its top-k SKUs by settles. Budget, balance
    floor, inventory cap, max_total_units, max_unit_cost and the below-market filter are unchanged.
    v1.23: shop.max_spend_frac (optional) sets one run's cap to that share of the balance, so the per-run spend
    grows and shrinks with cash. v1.23b (성수님 2026-11-05): when it is set max_spend_per_run is not used (no
    absolute ceiling); the icrl stock lane tunes the share (action space 0.15-0.35).
    v2.0a (Patch 3.2/3.3, 2026-11-05): restock costs follow the inventory.json surge factors (cost cap, below-market
    check, worst spend) and goods SKUs rank by market margin per hour (margin_rate) before settles."""
    sp = pol["shop"]
    bal = int(inv.get("balance") or 0)
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired"]
    if liquidating(inv, pol):
        return [], {"why": "liquidation", "balance": bal, "units": len(units)}
    why, info = restock_gate(inv, pol, activity, now)
    if why:
        return [], {"why": why, "units": len(units), **info}
    # the market refuses a whole lot whose cost is above the balance: plan with the worst cost (75% of list)
    spend_cap = sp["max_spend_per_run"]
    if sp.get("max_spend_frac") is not None:            # v1.23 (성수님 2026-11-05): per-run spend follows the balance
        spend_cap = int(float(sp["max_spend_frac"]) * bal)   # v1.23b: no absolute ceiling, max_spend_per_run unused
    budget = min(spend_cap, bal - sp["balance_floor"])
    inv_cost = inventory_cost(inv)[0]
    frac = sp.get("max_inventory_cost_frac")
    if frac is not None:                                 # v1.7 (a): inventory at cost + this run <= frac x balance
        budget = min(budget, int(float(frac) * bal) - inv_cost)
    if budget <= 0 or len(units) >= sp["max_total_units"]:
        why = "full" if len(units) >= sp["max_total_units"] else (
            "inventory_cap" if frac is not None and int(float(frac) * bal) - inv_cost <= 0 else "budget")
        return [], {"why": why, "budget": budget, "units": len(units), "inventory_cost": inv_cost}
    have, held = {}, {}                                 # units on hand per category, and per SKU (v1.30)
    for u in units:
        have[u.get("category")] = have.get(u.get("category"), 0) + 1
        held[u.get("sku")] = held.get(u.get("sku"), 0) + 1
    by_cat = {}
    for sku, x in catalog.items():
        by_cat.setdefault(x.get("category"), []).append((sku, x))

    own_v = own_velocity(activity, catalog, pol, now)
    asked = own_asked(activity, now) if sp.get("restock_asked_first", True) else {}   # v2.13
    cost_frac = expected_cost_frac(units, sp) if sp.get("skip_below_market") else None
    goods_cf = sp.get("goods_cost_frac") if cost_frac else None   # v2.8, see below_market_cost_frac
    surge = surge_factors(inv)                           # v2.0a (Patch 3.2): hot SKUs cost more to restock

    prio = [str(x) for x in (sp.get("restock_priority") or [])]      # v1.20a: categories bought first

    def score(d):
        for i, key in enumerate(prio):                   # v1.20a: full path or leaf name, in list order
            if key in (d["category"], leaf(d["category"])):
                return (-2, i)
        if taste and taste.get(d["category"]):          # v1.19: adopted taste forecast, ranking only
            return (-1, -float(taste[d["category"]]))
        s = _stat(stats, "category", d["category"])
        v = ((s or {}).get("velocity_per_h") or 0) + own_v.get(d["category"], 0)
        if v:
            return (0, -v)
        return (1, d.get("median_price") or 0)          # no data yet: cheap, fast-turning categories first

    plan, notes = [], []                                 # (sku, catalog row, wanted qty, breadth qty) in rank order
    warns = []                                           # v1.14: goods cap turned off by an unreadable storage value
    for d in sorted(demand, key=score):
        cat, kind = d["category"], d.get("kind") or "goods"
        c = resolve(pol, cat, kind)
        # v1.31 (2026-11-05): a SKU's units above the category level (stock_target / skus_per_category, rounded up)
        # do not fill the need, so a slow SKU's pile cannot take the fast SKUs' places; the food and storage caps
        # below still count every unit on hand. At 07:04 labpc held 11 sofa G5e3784e6 (57만, 3 h market 43 settles)
        # and 2 G00327e80, so the sofa need was 3 while G8ec51935 (232만) settled 235 times in 3 h market-wide (24 at
        # our shop, each unit sold in about 6 min): the level is 6, so the need is 8.
        lvl = math.ceil(c["stock_target"] / max(1, int(c.get("skus_per_category", sp.get("skus_per_category", 1)))))
        over = sum(max(0, held.get(sku, 0) - lvl) for sku, _ in by_cat.get(cat, []))
        need = c["stock_target"] - have.get(cat, 0) + over
        if need <= 0:
            continue
        cands = [(sku, x) for sku, x in by_cat.get(cat, []) if x.get("list_price")
                 and x["list_price"] * 0.75 * surge.get(sku, 1.0) <= sp["max_unit_cost"]
                 and (kind != "food" or (x.get("shelf_hours") or 0) >= c.get("min_shelf_hours", 0))]
        if not cands:
            continue
        # the SKU nearest the category median: orders price SKUs 0.5-1.5x a reference price.
        # v1.4 (G): food - among SKUs priced 0.5-1.5x the median, the longest shelf life first: the grade is drawn
        # at supply (C = under 45% of shelf life left) and then decays, so a long-lived SKU stays A/B (what buyers'
        # accept asks for) for more hours. skus_per_category > 1 spreads the units over several SKUs (an order
        # accepts only some SKUs of its category).
        # v1.6 (CR v1.5 item 6): SKUs the market has seen settle first. A settle means the SKU was on some buyer's
        # allowlist (orders accept only some SKUs of a category; the ledger carries no allowlist), so settles per SKU
        # (aggregates sku(): settles_all, all shops) are the revealed allowlist demand. No data (cold start, every
        # mock first restock): the v1.4 order below, unchanged.
        med = d.get("median_price") or 0
        sold = {t[0]: int((_stat(stats, "sku", t[0]) or {}).get("settles_all") or 0) for t in cands}
        near = [t for t in cands if not med or 0.5 * med <= t[1]["list_price"] <= 1.5 * med or sold[t[0]] > 0
                or t[0] in asked] or cands
        # v2.13: SKUs buyers asked our shop for and we lacked come first, most asked first (then the old order)
        if kind == "food":
            order_ = sorted(near, key=lambda t: (-asked.get(t[0], 0), -sold[t[0]], -(t[1].get("shelf_hours") or 0), abs(t[1]["list_price"] - med), t[0]))
        else:                                            # v2.0a: goods by market margin per hour, then settles
            rate = {t[0]: margin_rate(t[0], t[1], stats, surge.get(t[0], 1.0)) for t in near}
            order_ = sorted(near, key=lambda t: (-asked.get(t[0], 0), -rate[t[0]], -sold[t[0]], abs(t[1]["list_price"] - med), t[0]))
        if any(t[0] in asked for t in order_[:3]):
            notes.insert(0, f"{cat}: asked first {[(t[0], round(asked[t[0]], 1)) for t in order_[:3] if t[0] in asked]}")
        if cost_frac:                                    # v1.18: SKUs the market settles under our cost + margin
            low = [t[0] for t in order_
                   if below_market(t[0], t[1], stats,
                                   surge_cost_frac(below_market_cost_frac(cost_frac, kind, goods_cf), kind,
                                                   surge.get(t[0], 1.0)),
                                   c["min_margin"])]
            if low:
                order_ = [t for t in order_ if t[0] not in low]
                notes.append(f"{cat}: market p50 under cost "
                             f"{below_market_cost_frac(cost_frac, kind, goods_cf):.2f} + margin: skip {low[:3]}")
                if not order_:
                    continue
        if kind != "food" and any(rate.values()):        # v2.0a
            notes.append(f"{cat}: by margin/h (만) {[(t[0], round(rate[t[0]] / 1e4)) for t in order_[:3]]}")
        elif any(sold.values()):
            notes.append(f"{cat}: by settles {[(t[0], sold[t[0]]) for t in order_[:3]]}")
        if kind == "food":                               # v1.7 (b): no deeper than expected sell-through
            cap = food_cap(cat, order_[0][1], c, sp, stats, own_v.get(cat, 0))
            if cap is not None:
                need = min(need, cap - have.get(cat, 0))
                if need <= 0:
                    notes.append(f"{cat}: food cap {cap}")
                    continue
        else:                                            # v1.13 (M13): goods no deeper than the storage horizon
            cap = goods_cap(cat, c, sp, stats, own_v.get(cat, 0), inv.get("storage_per_day"), warns)
            if cap is not None:
                need = min(need, cap - have.get(cat, 0))
                if need <= 0:
                    notes.append(f"{cat}: goods storage cap {cap}")
                    continue
        qty_all = max(1, min(c["qty_per_buy"], need, 20))
        # v1.20a: skus_per_category per category (categories[...]) over the shop default. With the shop default 1
        # only order_[0] was ever bought: G1515d951 (2nd by settles in 90 of 92 mattress plans) got 0 units.
        k = max(1, min(int(c.get("skus_per_category", sp.get("skus_per_category", 1))), qty_all, len(order_)))
        # v1.30: the units bring the k SKUs up to one level of units on hand (level_fill), so a SKU already above
        # it gets none; with nothing on hand the split is the old even one
        for (sku, x), qty in zip(order_[:k], level_fill(qty_all, [held.get(t[0], 0) for t in order_[:k]])):
            if qty:
                plan.append((sku, x, qty, max(1, min(qty, int(c.get("breadth_qty") or qty)))))
    # v1.6 (CR v1.5 item 6): two passes. Pass 1 = breadth: every ranked category gets up to breadth_qty units (the
    # v1.5 per-category quantities), as many lines as max_lines_per_run allows. Pass 2 = depth: the remaining budget
    # tops the pass-1 lines up to qty_per_buy, in rank order. Without breadth_qty pass 1 buys the full quantity and
    # pass 2 does nothing (v1.5 behaviour).
    got, spent = {}, 0
    max_lines = min(int(sp["max_lines_per_run"]), int(info.get("lines_fit") or sp["max_lines_per_run"]))
    for sku, x, qty, q1 in plan:
        if len(got) >= max_lines:
            break
        worst = int(math.ceil(x["list_price"] * 0.75 * surge.get(sku, 1.0))) * q1   # 75% of list (agent.md) x surge
        if spent + worst > budget:
            notes.append(f"skip {sku}: worst cost {worst} over budget left {budget - spent}")
            continue
        got[sku] = q1
        spent += worst
    for sku, x, qty, q1 in plan:
        if sku not in got or qty <= got[sku]:
            continue
        unit = int(math.ceil(x["list_price"] * 0.75 * surge.get(sku, 1.0)))
        add = min(qty - got[sku], max(0, (budget - spent) // unit))
        got[sku] += add
        spent += add * unit
    buy = [{"sku": sku, "qty": q} for sku, q in got.items()]
    out = {"budget": budget, "worst_spend": spent, "units": len(units), "inventory_cost": inv_cost,
           "notes": notes[:5], **info}
    if warns:                                            # v1.14: one line per distinct reason (not per category)
        out["warnings"] = list(dict.fromkeys(warns))
    return buy, out


# --- buyer side

def own_shop(shop, pol, own=None):
    """Same GitHub owner (Lee-sungsoo) cannot settle with itself. `own` = owner.resolve() result (names, urls,
    pubkeys, from policy + /api/shops + kit key file); without it, the policy names and pubkeys only. Names compare after owner.norm_name (case,
    outer spaces, '_'/'-'/inner spaces); an owner/github field on the row itself (string, {login}, or a
    github.com URL) also matches."""
    import owner as OW
    o = pol.get("owner") or {}
    gh = OW.norm_name(o.get("github"))
    names = {OW.norm_name(n) for n in (o.get("own_shop_names") or [])} | \
        {OW.norm_name(n) for n in (own or {}).get("names") or ()}
    if OW.norm_name(shop.get("name")) in names - {""}:
        return True
    if gh and OW.owner_login(shop) == gh:
        return True
    urls = {OW.norm_url(u) for u in (own or {}).get("urls") or ()}
    if shop.get("url") and OW.norm_url(shop.get("url")) in urls:
        return True
    # v1.4: public key, from the row itself (/api/shops) or from the url -> pubkey map of the last /api/shops
    # read (the kit's shops.json drops pubkey). Our keys: policy own_shop_pubkeys + kit key file + learned.
    mine = set((own or {}).get("own_pubkeys") or ()) | {str(k).strip() for k in o.get("own_shop_pubkeys") or []}
    key = shop.get("pubkey") or ((own or {}).get("pubkeys") or {}).get(OW.norm_url(shop.get("url")))
    return bool(key) and str(key).strip() in mine


def own_buyer(name, pol):
    import owner as OW
    o = pol.get("owner") or {}
    n = OW.norm_name(name)
    return bool(n) and n in {OW.norm_name(x) for x in (o.get("own_buyer_names") or []) + [o.get("github") or ""]}


def buyer_terms(order, pol, relax=0.0):
    """Buyer settings for this order after the min-volume guard: max_pay_frac moves toward
    volume.relax_max_pay_frac and step_frac grows by up to 50% (r = 1). Still capped by the budget (mandate)."""
    c = resolve(pol, order.get("category"), order.get("kind") or "goods", side="buyer")
    r = max(0.0, min(1.0, relax))
    v = pol.get("volume") or {}
    hi = max(c["max_pay_frac"], float(v.get("relax_max_pay_frac", c["max_pay_frac"])))
    c["max_pay_frac"] = c["max_pay_frac"] + r * (hi - c["max_pay_frac"])
    c["step_frac"] = c["step_frac"] * (1 + 0.5 * r)
    if "accept_ask_frac" in c:                          # v1.21 (2026-11-04): moves toward the relaxed max_pay_frac
        a = float(c["accept_ask_frac"])
        c["accept_ask_frac"] = a + r * (c["max_pay_frac"] - a)
    return c


# v1.22 (round 3 labpc logs, 589 food checkout refusals for shelf life): refusal rate by r = use_within_hours /
# shelf_hours. r < 0.25: 1.7%, < 0.5: 11%, < 0.75: 34%, else 64%.
SHELF_REFUSE_STEPS = ((0.25, 0.017), (0.5, 0.11), (0.75, 0.34))
SHELF_REFUSE_TOP = 0.64


def p_refuse(use_within_hours, shelf_hours):
    """v1.22: chance that a food checkout is refused for shelf life (step function of r above), or None when
    either number is missing or not positive."""
    ok = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
    if not ok(use_within_hours) or not ok(shelf_hours):
        return None
    r = use_within_hours / shelf_hours
    return next((p for hi, p in SHELF_REFUSE_STEPS if r < hi), SHELF_REFUSE_TOP)


def choose_offers(order, shops, catalog, pol, reputation=None, own=None, relax=0.0):
    """Rank (shop, sku) pairs by expected surplus: list_price * (1 - target pay share) weighted by the shop's
    claim_honest / not-broken record. Own-owner shops are dropped (same GitHub owner cannot settle).
    v1.22: food scores are also weighted by 1 - p_refuse(use_within_hours, catalog shelf_hours) (skipped when
    either is missing)."""
    acceptable = set(order.get("acceptable") or [])
    c = buyer_terms(order, pol, relax)
    food = (order.get("kind") or "goods") == "food"
    budget = int(order.get("budget") or 0)
    out = []
    for s in shops:
        if own_shop(s, pol, own):
            continue
        rep = (reputation or {}).get(s.get("url")) or (reputation or {}).get(s.get("name")) or {}
        honest = rep.get("claim_honest_rate", 1.0)
        if rep.get("settled", 0) >= 5 and honest < pol["buyer"]["min_claim_honest_rate"]:
            continue
        broken = rep.get("broken_rate", 0.0)
        for off in s.get("offers") or []:
            sku = off.get("sku")
            if acceptable and sku not in acceptable or not off.get("qty"):
                continue
            lp = int((catalog.get(sku) or {}).get("list_price") or 0)
            if lp <= 0:
                continue
            target = min(budget, int(lp * c["max_pay_frac"]))
            if target <= 0:
                continue
            exp = (lp - int(lp * c["open_frac"])) * honest * (1 - broken)
            pr = p_refuse(order.get("use_within_hours"), (catalog.get(sku) or {}).get("shelf_hours")) if food else None
            if pr is not None:
                exp *= 1 - pr
            out.append({"shop": s, "sku": sku, "name": off.get("name") or sku, "list_price": lp,
                        "max_pay": target, "open": max(1, int(lp * c["open_frac"])), "score": exp,
                        "broken_rate": broken})
    out.sort(key=lambda r: (-r["score"], r["sku"]) + shop_tie_key(r["shop"]))
    return out


def shop_tie_key(shop):
    """v1.6 (CR v1.5 minor 3): deterministic tie-break between equal scores: normalised shop name, then the URL
    without scheme and port (owner.norm_url minus any :port), then the raw URL. v1.5 sorted on the raw URL, so in
    the mock (random local ports) equal-score shops were ordered by port and the rival scenario differed per run."""
    import re
    import owner as OW
    u = OW.norm_url(shop.get("url") or "")
    host, _, path = u.partition("/")
    return (OW.norm_name(shop.get("name") or ""), re.sub(r":\d+$", "", host) + ("/" + path if path else ""),
            str(shop.get("url") or ""))


HAGGLE_ROUNDS = 8                                       # hooks/market_buyer.haggle sends at most this many offers


def next_offer(cand, pol, order, rounds, last_ask, relax=0.0):
    """Buyer's next int offer: open low, step up, never above max_pay (<= budget, < list price).
    v1.21 (2026-11-04): optional buyer accept_ask_frac a (per kind, per category via resolve; relax moves it toward
    the relaxed max_pay_frac in buyer_terms). From round 1 a shop ask is accepted when <= min(max_pay, list x a), or
    when <= max_pay in the final round, rounds >= min(max_rounds, HAGGLE_ROUNDS) - 1 (haggle stops at HAGGLE_ROUNDS
    offers and does not read max_rounds, so the final round is never later than the last one haggle plays);
    otherwise counter, never above the ask. Ledger (analysis/buyer_surplus_20261104.json): we paid 0.986 x the SKU
    median because any ask under the cap was accepted at round 1. Key absent = threshold max_pay = the v1.20 rule."""
    c = buyer_terms(order, pol, relax)
    if last_ask is not None and rounds > 0:
        thr = cand["max_pay"]
        if "accept_ask_frac" in c:
            thr = min(thr, int(cand["list_price"] * c["accept_ask_frac"]))
        final = rounds >= min(int(c["max_rounds"]), HAGGLE_ROUNDS) - 1
        if last_ask <= thr or (final and last_ask <= cand["max_pay"]):
            return int(last_ask)
    o = int(cand["open"] + rounds * c["step_frac"] * cand["list_price"])
    o = min(o, cand["max_pay"])
    if last_ask is not None and o >= last_ask:          # never offer above the shop's ask (round 0 included)
        return int(last_ask)
    return o


def accept_policy(order, pol, relax=0.0):
    """What the kit checks before paying. Min-volume guard: at relax >= volume.relax_grade_c_at grade C is
    accepted too. Shelf life for food (use_within_hours + margin) is never relaxed: food that spoils before use
    gives no surplus. v1.4: no cert relaxation (CR v1.2 new issue 3: with grades set, kit check_accept refuses a
    checkout without a certificate anyway, "no certificate to show the grade"; relax_cert_at had no effect)."""
    kind = order.get("kind") or "goods"
    c = resolve(pol, order.get("category"), kind, side="buyer")
    a = dict(c["accept"])
    v = pol.get("volume") or {}
    out = {"cert": bool(a.get("cert", True))}
    if a.get("grades"):
        out["grades"] = sorted({str(g).strip().upper() for g in a["grades"]})
        if relax >= v.get("relax_grade_c_at", 2.0) and "C" not in out["grades"]:
            out["grades"].append("C")
    if kind == "food":
        need = order.get("use_within_hours")
        if isinstance(need, (int, float)):
            out["min_hours_left"] = need + a.get("hours_margin", 0)
    elif a.get("warranty_hours"):
        out["warranty_hours"] = a["warranty_hours"]
    return out


def review_for(deal, broken_names=()):
    """Stars from settled facts only: claim honesty, grade, shelf life vs need, price vs list."""
    s = deal.get("settled") or {}
    o = deal.get("order") or {}
    stars, why = 5, []
    if s.get("claim_honest") is False:
        stars -= 2
        why.append(f"claimed {s.get('claim')} but was {s.get('grade')}")
    if s.get("grade") == "C":
        stars -= 1
        why.append("grade C")
    if s.get("expires_at") and o.get("use_within_hours"):
        left = (s["expires_at"] - time.time()) / 3600
        if left < o["use_within_hours"]:
            stars -= 2
            why.append(f"{left:.0f} h shelf life left, needed {o['use_within_hours']}")
    if s.get("name") and s.get("name") in broken_names:
        stars -= 2
        why.append("broke")
        if not s.get("warranty_hours"):  # v2.5: this item broke on us before and the shop gave no warranty
            stars -= 1
            why.append("no warranty")
    stars = max(1, min(5, stars))
    text = f"{s.get('name') or s.get('sku')}: grade {s.get('grade')}, claim {'honest' if s.get('claim_honest') is not False else 'not honest'}"
    if why:
        text += "; " + ", ".join(why)
    return stars, text[:280]


def _stat(stats, kind, key):
    if stats is None or key is None:
        return None
    f = getattr(stats, kind, None)
    try:
        return f(key) if callable(f) else None
    except Exception:
        return None
