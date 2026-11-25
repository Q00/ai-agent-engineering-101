"""Round 3: the shop's stock, read from the .market/inventory.json the kit keeps (every 10 s and after each supply).

The shop runs on Render with --deployed: the kit in the same service refreshes the file but serves no checkout, so
the seller (which unit to promise) and acp_shop (which unit to ship) share one StockBook in this process.
The file can be 10 s old, so the book also remembers what it has promised and sold since.
"""
import json
import math
import os
import re
import time
import threading
import functools
import hashlib
import copy
import uuid
from pathlib import Path

ECON_ROUND = 3
HOLD_SECONDS = 300      # a talk silent this long frees its unit (each message renews it); 900 s hid stock for most of a window
PAY_SECONDS = 120       # after the deal: the unit waits this long for its payment (paid deals settle within seconds), then is on sale again
LATE_SECONDS = 900      # ... and is still credited if its unit leaves the stock this long after the wait
PAY_MARGIN_HOURS = 0.1  # food must outlast the buyer's need by this much (time to pay), GPT Pro §3
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b|시간)", re.I)
SHELF_WORDS = re.compile(r"shelf|last|expir|fresh|after payment|유통|소비|남", re.I)
GRADES = re.compile(r"\bgrades?\s*(?:of\s*|:\s*)?([ABC](?:\s*(?:,|/|or|and)\s*[ABC])*)\b(\s*(?:or (?:better|above|higher)|이상))?"
                    r"|\b((?-i:[ABC]))\s*(?:등급|grade\b)(\s*(?:or (?:better|above|higher)|이상))?", re.I)


POLICY = re.compile(r"\{[^{}]*\}")


def policy_needs(text):
    """The needs a buyer sends as JSON in its words: 'Required checkout policy: {"cert": true, "grades": ["A", "B"],
    "min_hours_left": 42.05}' (4 such asks from 09:26 to 13:30 on 11/06; the words alone read none of it, GPT Pro 11/06
    07:30 S2). {"hours", "grades", "cert", "skus"}, each None/empty when not given."""
    hours, grades, cert, skus = [], set(), False, set()
    for m in POLICY.finditer(text or ""):
        try:
            d = json.loads(m.group(0))
        except (ValueError, RecursionError):
            continue
        if not isinstance(d, dict):
            continue
        for k in ("min_hours_left", "min_shelf_hours", "shelf_hours_left"):
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 10 ** 5:     # not inf, not 1e400
                hours.append(float(v))
        g = d.get("grades", d.get("grade"))
        for x in ([g] if isinstance(g, str) else g if isinstance(g, list) else []):
            if isinstance(x, str) and x.strip().upper() in ("A", "B", "C"):
                grades.add(x.strip().upper())
        cert = cert or d.get("cert") is True or d.get("certificate") is True
        for k in ("sku", "skus"):
            v = d.get(k)
            skus |= {x for x in ([v] if isinstance(v, str) else v if isinstance(v, list) else []) if isinstance(x, str) and SKU.fullmatch(x)}
    return {"hours": max(hours) if hours else None, "grades": grades or None, "cert": cert, "skus": skus}


def needs(text):
    """What the buyer's words ask of a unit: {"skus", "hours", "grades", "cert"}. SKUs named are the only acceptable ones;
    hours is the shelf life asked for (food), grades the grades allowed. Words about a warranty's hours are not shelf life.
    A JSON policy in the words (policy_needs) adds to them: the longer shelf life, its grades, its certificate."""
    t = text or ""
    pol = policy_needs(t)
    hours = [float(m.group(1)) for m in HOURS.finditer(t)
             if SHELF_WORDS.search(t[max(0, m.start() - 40):m.end() + 25])
             and not re.search(r"warrant|guarantee|보증", t[max(0, m.start() - 25):m.end() + 12], re.I)]
    grades = set()
    for m in GRADES.finditer(t):
        got = set(re.findall(r"[ABC]", (m.group(1) or m.group(3)).upper()))
        if m.group(2) or m.group(4):          # "grade B or better": B and every grade above it
            got |= {g for g in "ABC" if g <= max(got)}
        grades |= got
    if pol["hours"] is not None:
        hours.append(pol["hours"])
    if pol["grades"]:
        grades = pol["grades"]          # the policy states the grades outright: it wins over grade words around it
    return {"skus": set(SKU.findall(t)) | pol["skus"], "hours": max(hours) if hours else None, "grades": grades or None,
            "cert": bool(re.search(r"certif|cert\s*=\s*true|인증서", t, re.I)) or pol["cert"]}


def unfit(u, need, now=None):
    """Why this unit cannot serve this buyer, or None: SKU, shelf life left >= need + PAY_MARGIN_HOURS, grade, certificate."""
    if need["skus"] and u["sku"] not in need["skus"]:
        return f"{u['sku']} is not the SKU asked for"
    if need["hours"] is not None and u.get("expires_at"):
        left = (u["expires_at"] - (now or time.time())) / 3600
        if left < need["hours"] + PAY_MARGIN_HOURS:
            return f"{left:.1f} h of shelf life left, {need['hours']:g} h asked"
    if need["grades"] and u.get("grade") not in need["grades"]:
        return f"grade {u.get('grade')}, {'/'.join(sorted(need['grades']))} asked"
    if need["cert"] and not u.get("cert"):
        return "no certificate"
    return None


def market_dir():
    return Path(os.environ.get("MARKET_DIR", ".market"))


def load_inventory():
    try:
        inv = json.loads((market_dir() / "inventory.json").read_text(encoding="utf-8"))
        return inv if isinstance(inv, dict) else {}
    except (OSError, ValueError):
        return {}


def last_segment(category):
    return (category or "").split(">")[-1].lower()


def expired(u, now=None):
    return u.get("grade") == "expired" or bool(u.get("expires_at") and u["expires_at"] <= (now or time.time()))


MARKDOWN = {"clear_hours": 24, "min_floor_share": 0.1, "stale_days": 3, "stale_per_day": 0.1, "end_hours": 48,
            "below_cost": ["food_expiry", "stale_goods", "round_end"]}


def markdown(u, cfg=None, ends_at=None, now=None):
    """(lowest price for this unit, share of cost it keeps, why). Selling below cost beats what waiting brings: food past
    its date is lost at cost, and stock left when the round ends counts as a loss. The numbers are starting values in
    pricing.json "markdown"; Round 3's own data has to judge them. "below_cost" lists the reasons allowed to go under
    cost (food_expiry, stale_goods, round_end): the shop starts with food near its date only."""
    cfg, now = {**MARKDOWN, **(cfg or {})}, now or time.time()
    allowed = set(cfg["below_cost"])
    cost, factors = math.ceil(u["cost"]), [(1.0, "")]
    supplied = u.get("supplied_at") or now
    if u.get("expires_at") and "food_expiry" in allowed:
        left, life = (u["expires_at"] - now) / 3600, (u["expires_at"] - supplied) / 3600
        clear = min(cfg["clear_hours"], max(life, 1) * 0.5)
        if left < clear:
            factors.append((max(0.0, left / clear), f"{left:.1f} h of shelf life left"))
    elif not u.get("expires_at") and "stale_goods" in allowed:
        over = (now - supplied) / 86400 - cfg["stale_days"]
        if over > 0:
            factors.append((max(0.0, 1 - over * cfg["stale_per_day"]), f"{over + cfg['stale_days']:.1f} days in stock"))
    if ends_at and "round_end" in allowed:
        left = (ends_at - now) / 3600
        if left < cfg["end_hours"]:
            factors.append((max(0.0, left / cfg["end_hours"]), f"{left:.1f} h to the end of the round"))
    keep, why = min(factors, key=lambda f: f[0])
    least = min(cost, math.ceil((u.get("list_price") or 0) * cfg["min_floor_share"]))
    return max(least, round(cost * keep)), keep, why


_rank_failed = []      # logged once


def _rank(rank, u):
    """rank(u) as a number for sorting; a unit it cannot rank goes last among its SKU."""
    try:
        r = rank(u)
    except Exception as e:  # a broken rank must not lose the sale: fall back to the oldest unit
        if not _rank_failed:
            _rank_failed.append(1)
            print(f"[shop_stock] unit rank failed, oldest unit first: {type(e).__name__}: {e}", flush=True)
        return float("inf")
    return r if isinstance(r, (int, float)) and not isinstance(r, bool) and math.isfinite(r) else float("inf")


def canonical_task(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def market_unit(value):
    return canonical_task(value) or bool(isinstance(value, str) and re.fullmatch(r'u-[0-9a-f]{12}', value))


def synchronized(fn):
    @functools.wraps(fn)
    def call(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return call


class StockBook:
    """A unit is free, promised to a talk (held, renewed by each message), agreed and waiting for its payment (held for
    PAY_SECONDS), or sold. Payment shows as the unit leaving inventory.json (the kit's checkout) or mark_sold (ours):
    on_paid(info) is called then, on_unpaid(info, why) when the wait runs out or the checkout is cancelled."""

    def __init__(self, load=load_inventory):
        self._lock = threading.RLock()
        self._generation = 0
        self.generations = {}
        self.load = load
        self.held = {}      # unit -> time promised (renewed); a hold lasts HOLD_SECONDS, or PAY_SECONDS once agreed
        self.owner = {}     # unit -> task that holds it
        self.agreed = {}    # unit -> {"task", "at", "expires_at", ...} deals waiting for their payment
        self.late = {}      # unit -> a deal whose wait ran out, still watched for a late payment
        self.sold = set()
        self.on_paid = self.on_unpaid = None

    def economy(self):
        """True from Round 3 on. Before that the shop keeps selling from catalog.json."""
        return self.load().get("round", 0) >= ECON_ROUND

    def grade(self, unit):
        """The unit's grade now (a food unit's grade drops while it waits), or None if it is gone."""
        return next((u.get("grade") for u in self.load().get("units", []) if u["unit"] == unit), None)

    def settle(self, inv_units=None, now=None):
        """Agreed deals: paid when the unit left the stock while it was still good, unpaid when the wait ran out. A deal
        whose wait ran out stays watched for LATE_SECONDS (the kit's checkout tells this process nothing, and a slow buyer
        may still pay): its unit is on sale again, and if it leaves the stock unclaimed by a newer deal, it was paid."""
        if not self.agreed and not self.late:
            return
        now = now or time.time()
        present = {u["unit"]: u for u in (inv_units if inv_units is not None else self.load().get("units", []))}

        def gone(unit, info):
            u = present.get(unit)
            if u is not None:
                info["seen"], info["bad"] = now, expired(u, now)
                return False
            return True

        def paid_or_not(unit, info, late=False):
            if info.get("bad") or (info.get("expires_at") and info["expires_at"] <= info.get("seen", info["at"])):
                self._call(self.on_unpaid, info, "expired before payment")
            else:
                self.sold.add(unit)
                self._call(self.on_paid, dict(info, late=late))

        for unit, info in list(self.agreed.items()):
            if gone(unit, info):
                del self.agreed[unit]
                self._drop(unit)
                self.late.pop(unit, None)               # the newer deal on this unit is the one that was paid
                paid_or_not(unit, info)
            elif now - self.held.get(unit, info["at"]) >= PAY_SECONDS:     # a checkout being paid renews the hold
                del self.agreed[unit]
                self._drop(unit)
                self.late[unit] = dict(info, lapsed=now)
                self._call(self.on_unpaid, info, f"not paid within {PAY_SECONDS} s (watched {LATE_SECONDS // 60} min more)")
        for unit, info in list(self.late.items()):
            if now - info["lapsed"] > LATE_SECONDS:
                del self.late[unit]
            elif gone(unit, info) and unit not in self.agreed:
                del self.late[unit]
                self._drop(unit)
                paid_or_not(unit, info, late=True)

    def available(self, unit, task):
        """True when this talk may still sell this unit: in stock, good, not sold, not promised or agreed to another talk."""
        self.units()                                    # settles payments and lets run-out holds go first
        u = next((u for u in self.load().get("units", []) if u["unit"] == unit), None)
        if u is None or expired(u, time.time()) or unit in self.sold:
            return False
        return unit not in self.held or self.owner.get(unit) in (None, task)

    @staticmethod
    def _call(fn, *args):
        if fn is not None:
            fn(*args)

    def _drop(self, unit):
        self.held.pop(unit, None)
        self.owner.pop(unit, None)
        self.generations.pop(unit, None)

    def units(self):
        """Units free to promise: in stock, not expired, not promised, not sold since the file was written."""
        now = time.time()
        inv = self.load().get("units", [])
        self.settle(inv, now)
        for unit, t in list(self.held.items()):
            if now - t >= (PAY_SECONDS if unit in self.agreed else HOLD_SECONDS):
                self._drop(unit)
        return [u for u in inv if not expired(u, now) and u["unit"] not in self.held and u["unit"] not in self.sold]

    def pick(self, text, now=None, rank=None, *, allow_grade_preference=False, selection=None, add_grades=None, units=None):
        """(unit, None) for what the buyer asked (SKU first, then name, then category): among the units that fit the buyer's
        needs (needs()/unfit()), the one that expires first. Among units that never expire (goods), rank(u) (the seller
        passes the unit's floor) comes before the oldest: 11/06 21:2x the oldest G24f5f979 cost 0.74 of list after a
        surge, so every quote opened at its floor 0.84 while the market sold the SKU at 0.70 and four units of ours with
        floors near 0.68 were never offered. (None, why) when none does."""
        want, need = text.lower(), needs(text)
        if add_grades and need["grades"]:      # G-read (grade_read.py, default off): a grade the words name but GRADES drops
            need = dict(need, grades=need["grades"] | set(add_grades))
        units = self.units() if units is None else units      # G-read: both arms' picks on one units() read
        for matches in (lambda u: u["sku"].lower() in want,
                        lambda u: (u.get("name") or "\0").lower() in want,
                        lambda u: (last_segment(u.get("category")) or "\0") in want):
            fit = [u for u in units if matches(u)]
            if not fit:
                continue
            ok = [u for u in fit if unfit(u, need, now) is None]
            if ok:
                return min(ok, key=lambda u: (u.get("expires_at") or float("inf"),
                                              0 if rank is None or u.get("expires_at") else _rank(rank, u),
                                              u.get("supplied_at") or 0)), None
            if allow_grade_preference:
                from seller_grade_preference import preference_needs
                fallback=preference_needs(text)
                extra=[u for u in fit if unfit(u,fallback,now) is None] if fallback else []
                if extra:
                    chosen=min(extra,key=lambda u:(u.get("expires_at") or float("inf"),
                               0 if rank is None or u.get("expires_at") else _rank(rank,u),
                               u.get("supplied_at") or 0))
                    if selection is not None:
                        selection.update(preference_used=True,baseline_miss=unfit(fit[0],need,now))
                    return chosen,None
            return None, unfit(fit[0], need, now)
        return None, "not in stock"

    def find(self, text):
        return self.pick(text)[0]

    def hold(self, unit, task=None):
        self._generation += 1
        self.generations[unit] = self._generation
        self.held[unit] = time.time()
        if task is not None:
            self.owner[unit] = task
        return dict(unit=unit, task=task, generation=self._generation, held_at=self.held[unit])

    def holder(self, unit):
        """The task holding this unit now, None when it is free (a run-out hold counts as free)."""
        self.units()
        return self.owner.get(unit) if unit in self.held else None

    def agree(self, unit, info):
        """The talk closed a deal on this unit: keep it for the payment."""
        self.hold(unit, info.get("task"))
        self.agreed[unit] = {"at": time.time(), **info}
        if info.get("task") is not None:
            self.owner[unit] = info["task"]

    def release(self, unit):
        info = self.agreed.pop(unit, None)
        self._drop(unit)
        if info is not None:
            self._call(self.on_unpaid, info, "checkout cancelled or refused")

    def mark_sold(self, unit):
        agreed = self.agreed.pop(unit, None)
        late = self.late.pop(unit, None)  # a newer sale consumes the old late-payment candidate too
        info = agreed or late
        self._drop(unit)
        self.sold.add(unit)
        if info is not None:
            self._call(self.on_paid, info)

    def ship(self, receipt, acceptable=None):
        """(unit dict, None) to ship for a receipt, or (None, reason). Same rule as the kit's AcpShop.pick_unit:
        the seller's unit if it still fits, else the first-expiring unit of a fitting SKU."""
        want = receipt.get("item")
        fits = lambda u: (u["sku"] in acceptable if acceptable else
                          u["sku"] == want or last_segment(u.get("category")) == str(want).lower())
        promised = receipt.get("unit")
        if promised:
            now = time.time()
            u = next((u for u in self.load().get("units", []) if u["unit"] == promised), None)
            if u and not expired(u, now) and promised not in self.sold and fits(u):
                return u, None
            return None, f"unit {promised} is not in stock for this order"
        fit = [u for u in self.units() if fits(u)]
        if not fit:
            return None, f"no {want} in stock"
        return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0)), None

    def pool_snapshot(self, now=None, *, inventory=None):
        """Detached whole inventory journal; deliberately never calls units()/settle()."""
        now = time.time() if now is None else now
        inv = copy.deepcopy(self.load() if inventory is None else inventory)
        if inv.get('round', 0) < ECON_ROUND or not isinstance(inv.get('units'), list):
            raise ValueError('inventory_missing')
        rows, seen = [], set()
        for u in inv['units']:
            uid = u['unit']
            if uid in seen:
                raise ValueError('duplicate_unit')
            seen.add(uid)
            for key in ('sku', 'kind', 'grade', 'cost', 'expires_at', 'cert'):
                if key not in u:
                    raise ValueError('inventory_field_missing')
            rows.append(dict(unit=uid, sku=u['sku'], kind=u['kind'], grade=u['grade'],
                             cost=u['cost'], expires_at=u['expires_at'],
                             cert_sha256=hashlib.sha256(json.dumps(u['cert'], sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
                             held_at=self.held.get(uid), owner=self.owner.get(uid),
                             generation=self.generations.get(uid), agreed=uid in self.agreed,
                             late=uid in self.late, sold=uid in self.sold))
        rows.sort(key=lambda x: x['unit'])
        encoded = json.dumps(rows, sort_keys=True, separators=(',', ':'), allow_nan=False)
        return dict(at=now, units=copy.deepcopy(rows),
                    sha256=hashlib.sha256(encoded.encode()).hexdigest(),
                    orphan_state=sorted((set(self.held) | set(self.agreed) | set(self.late) |
                                         self.sold) - seen))

    def compare_and_release_quote(self, token, *, rounds, shadow=True, now=None, inventory=None):
        """Compare own quote generation, then project or drop under the SAME state lock.

        No settlement, callbacks, agreement cancellation, or inventory renewal here.
        Caller must hold the first-turn lease; the runtime controller is shadow-only.
        """
        now = time.time() if now is None else now
        uid = token.get('unit')
        reason = None
        full_owner = canonical_task(token.get('task'))
        if not full_owner:
            reason = 'invalid_owner'
        elif not market_unit(uid):
            reason = 'invalid_unit'
        elif type(rounds) is not int or rounds != 0:
            reason = 'continuation'
        elif not token.get('task') or self.owner.get(uid) != token['task']:
            reason = 'owner_changed'
        elif self.generations.get(uid) != token.get('generation'):
            reason = 'generation_changed'
        elif self.held.get(uid) != token.get('held_at') or uid not in self.held:
            reason = 'hold_changed'
        elif uid in self.agreed:
            reason = 'agreed'
        elif uid in self.late:
            reason = 'late'
        elif uid in self.sold:
            reason = 'sold'
        elif not 0 <= now - self.held[uid] < HOLD_SECONDS:
            reason = 'hold_expired'
        else:
            inv = self.load() if inventory is None else inventory
            matches = [u for u in inv.get('units', []) if u.get('unit') == uid]
            if inv.get('round', 0) < ECON_ROUND or len(matches) != 1:
                reason = 'inventory_missing'
            elif expired(matches[0], now):
                reason = 'expired'
        if reason:
            return dict(reason=reason, released=False)
        if not shadow:
            self._drop(uid)
        return dict(reason='would_release' if shadow else 'released', released=not shadow,
                    remaining_seconds=HOLD_SECONDS - (now - token['held_at']))

    def quote_competitor(self, token, need, *, sku, warranty=0, required_warranty=0, inventory=None):
        """Current held unit would fit another complete demand; never alters the pool."""
        now = time.time()
        inv = copy.deepcopy(self.load() if inventory is None else inventory)
        snapshot = self.pool_snapshot(now, inventory=inv)
        state = self.compare_and_release_quote(token, rounds=0, shadow=True, now=now, inventory=inv)
        if state['reason'] != 'would_release':
            return dict(reason=state['reason'])
        u = next(u for u in inv['units'] if u['unit'] == token['unit'])
        if (u['sku'] != sku or unfit(u, need, now) is not None
                or (u.get('kind') == 'goods' and warranty < required_warranty)
                or (u.get('kind') == 'food' and required_warranty > 0)):
            return dict(reason='selection_guard')
        alternatives = [v['unit'] for v in inv['units']
                        if v['sku'] == sku and v['unit'] not in self.held and v['unit'] not in self.sold
                        and not expired(v, now) and unfit(v, need, now) is None]
        return dict(reason='inventory_compatible_candidate', economic_status='unknown',
                    pool_sha256=snapshot['sha256'], snapshot=snapshot,
                    free_alternatives=alternatives, selection_blocked=not alternatives)


# Every public reader/mutator and internal state helper shares one RLock.
# Callbacks run synchronously while locked: existing callbacks enqueue memory writes,
# never await another thread. Same-thread reentry is supported; no async lock spans.
for _method in ('economy', 'grade', 'settle', 'available', '_drop', 'units', 'pick',
                'find', 'hold', 'holder', 'agree', 'release', 'mark_sold', 'ship',
                'pool_snapshot', 'compare_and_release_quote', 'quote_competitor'):
    setattr(StockBook, _method, synchronized(getattr(StockBook, _method)))
