"""R3 seller: A2A shop that sells units from $MARKET_DIR/inventory.json. Pure-code pricing from policy.json.

    python hooks/seller.py --port {port}

- Finds the unit for the buyer's request (SKU in the text first, else category leaf or name), skipping units
  promised in another open negotiation.
- Prices: ask from policy.ask_price, concessions from policy.respond; never below cost (policy.guard_price is the
  last check before signing). Price is an int when signed, so the kit's checkout (int only) and sig_seller agree.
- Receipt adds unit, honest claim (= the unit's grade), cert shown, warranty_hours per policy (goods).
- v1.8 text: card/skill descriptions and the first reply (opening_text) state only what the receipt carries.
"""
import argparse
import atexit
import base64
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from common import volume_level, open_resident, P, Recorder, Ledger, hook_event, read_json  # noqa: F401
import buyer_log as BL   # v1.20 logging only (never read by a decision)

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

try:                                              # model seller bridge, on while <W>/a2/LIVE exists
    sys.path.append('/home/sungsoo/r3a2-code')
    from kernel import seller_bridge as A2
except Exception:                                 # noqa: BLE001
    A2 = None

ENV_NAME = os.environ.get("R3_SHOP_NAME", "lee-sungsoo")
NAME_FILE_MAX = 64


def resolve_shop_name(env_name=None, path=None):
    """Shop name: <W>/.r3/shop_name overrides the kit env R3_SHOP_NAME (a seller-only
    restart cannot change the inherited kit env). Missing/empty/invalid/too long/unreadable -> env value. Revert = rename
    the file to shop_name.off (no delete). Returns (name, source)."""
    env_name = env_name if env_name is not None else ENV_NAME
    p = Path(path or os.environ.get("R3_SHOP_NAME_FILE", Path(os.environ.get("R3_STATE", ".r3")) / "shop_name"))
    try:
        raw = p.read_bytes()
    except OSError:
        return env_name, "env"
    try:
        v = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return env_name, "env:invalid"
    if not v or len(v) > NAME_FILE_MAX or "\n" in v or not v.isprintable() or not re.search(r"[0-9A-Za-z]", v):
        return env_name, "env:invalid"
    return v, "file"


NAME, NAME_SOURCE = resolve_shop_name()
if NAME != ENV_NAME:                       # ledger/volume (same process) see both old and new name as our shop
    os.environ["R3_SHOP_NAME_PREV"] = ENV_NAME
    os.environ["R3_SHOP_NAME"] = NAME
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
SKU_RE = re.compile(r"\b(G[0-9A-Za-z]{5,})\b")
HOLD_S = 120               # a unit promised in an open negotiation is held this long, then freed (buyers walk away)
ACCEPT_HOLD_S = 600        # v2.3: a unit just sold is not offered again while its buyer checks out
REFUSED_HOLD_S = 6 * 3600  # v2.3: a unit the kit refused at checkout is held by an unpaid session; skip it this long
KIT_LOG = Path(os.environ.get("R3_KIT_LOG", "logs/market-kit.log"))
KIT_TAIL_BYTES = 1_000_000
REFUSED_RE = re.compile(r"^(\d\d):(\d\d):(\d\d) \[acp\] checkout refused: unit (u-[0-9a-f]+) is not in stock")
TICK_S = float(os.environ.get("R3_STATS_TICK_S", "15"))   # background refresh/checkpoint check; 0 = off


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    assert type(body["price"]) is int, "signed price must be int"
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


NEED_RE = re.compile(r"(?<!\d)(?<!\d[.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h|시간)(?![0-9A-Za-z])",
                     re.I)                                     # v2.1a: 1,000 hours -> 1000, 48h,72h -> 72
NEED_MARGIN_H = 0.25          # v2.1: the settle lands minutes after the quote
UNKNOWN_NEED_SHARE = 0.75     # v2.1: a food buyer needs 0.10/0.25/0.50/0.75 of the shelf; 0.75 covers every buyer
DECLINE_MIN_CHARS = 12        # v2.2: a shorter shop.decline_texts entry matches nothing ('the' declines no one)


def declined_phrase(text, pol):
    """v2.2: the shop.decline_texts entry the request carries (case and runs of whitespace ignored), else None. The
    list lives in policy.json, so a phrase is added or dropped without a restart; anything but a list of strings of
    12 characters or more matches nothing. One buyer's template 'Quote the total price. Require a market
    certificate.' left 1 star on all 63 of its paid deals with us in the 24 h to 11-05 14:40."""
    lst = (pol.get("shop") or {}).get("decline_texts")
    if not isinstance(lst, list):
        return None
    t = " ".join((text or "").split()).casefold()
    for e in lst:
        e = " ".join(e.split()).casefold() if isinstance(e, str) else ""
        if len(e) >= DECLINE_MIN_CHARS and e in t:
            return e
    return None


def stated_need_hours(text, names=()):
    """v2.1: shelf life the request asks for, in hours: the largest '<number> h / hour(s) / 시간' in it after SKU
    tokens and product `names` are removed ('I need it to last 180.0 hours: ... at least 198.0 hours' -> 198.0,
    'need: shelf life 541.0 h or more' -> 541.0; catalog G56d000c7 is named '... 48시간 구운계란 ...'). None when
    it names no hours. v2.1a: names and their own hour tokens come out case-insensitively, so '24h 숙성 김치' for
    '24H 숙성 김치' and a shortened '48시간 구운계란' state no need."""
    t = SKU_RE.sub(" ", text or "")
    names = sorted({n for n in names if n}, key=len, reverse=True)
    for nm in names:
        t = re.sub(re.escape(nm), " ", t, flags=re.I)
    for tok in sorted({m.group(0) for nm in names for m in NEED_RE.finditer(nm)}, key=len, reverse=True):
        t = re.sub(r"(?<!\d)(?<!\d[.,])" + re.escape(tok) + r"(?![0-9A-Za-z])", " ", t, flags=re.I)
    hs = [float(m.group(1).replace(",", "")) for m in NEED_RE.finditer(t)]
    return max(hs) if hs else None


def pick_food(food, need, shelf, now, min_frac=None):
    """v2.1 (24 h to 11-05 13:30 KST: 71 of our 75 two-star food reviews read 'Only N h of shelf life left; I
    needed M h'): ship a unit that lasts the buyer's need. A stated need keeps the units with hours left >=
    need + NEED_MARGIN_H; none -> None (no sale rather than a two-star review). An unknown need is taken as
    UNKNOWN_NEED_SHARE x the catalog shelf_hours; when no unit lasts that long, the unit with the most hours left.
    Among the units that last, the one that expires first (stock keeps rotating; our grade C food reviews 4.75).
    v2.10 (30 h to 11-09 11:00 KST, unstated-need food sales: under 0.5 of the shelf left 23-44% of reviews read
    'Only N h of shelf life left', 3.65-4.09 stars; 0.5 and over 0-9%): with min_frac set, the fallback only takes a
    unit with hours left >= min_frac x shelf_hours; none -> None (no sale)."""
    left = lambda u: P.hours_left(u, now)
    key = lambda u: (left(u), u.get("supplied_at") or 0)
    if need is not None:
        return min((u for u in food if left(u) >= need + NEED_MARGIN_H), key=key, default=None)
    ok = [u for u in food if shelf(u["sku"]) and left(u) >= UNKNOWN_NEED_SHARE * shelf(u["sku"]) + NEED_MARGIN_H]
    if ok:
        return min(ok, key=key)
    if min_frac:
        food = [u for u in food if shelf(u["sku"]) and left(u) >= min_frac * shelf(u["sku"])]
    return max(food, key=lambda u: (left(u), -(u.get("supplied_at") or 0)), default=None)


def sells_at_market(u, pol, stats, inv=None, now=None):
    """v2.6: True unless the SKU's market settle price (shop.ask_quantile, >= 5 settles) is below this unit's floor.
    11-07 19:30 KST: 33 of our 57 bed A units cost 0.75-0.85 of list, floor above the bed market median 0.72; the
    oldest-first pick offered them 109 times in an hour while cheaper units of the same SKU waited. Unknown -> True."""
    s = P._stat(stats, "sku", u.get("sku"))
    q = (pol.get("shop") or {}).get("ask_quantile") or "p75"
    if not s or s.get("n", 0) < 5 or not s.get(q):
        return True
    return P.floor_price(u, pol, inv, now) <= s[q]


def find_unit(text, inv, taken=(), pol=None, now=None, shelf=None, why=None, stats=None):
    """`why` (a dict, optional) gets need_h when food units match but none lasts the stated need.
    `stats` (v2.6, optional): goods units whose floor is above the SKU's market price go last."""
    now = time.time() if now is None else now
    want = text.lower()
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired" and u["unit"] not in taken
             and (pol is None or P.sellable(u, pol, now))]
    skus = {s.lower() for s in SKU_RE.findall(text)}
    named = [u for u in units if u["sku"].lower() in skus]
    fit = named or [u for u in units if P.leaf(u.get("category")).lower() in want
                    or (u.get("name") or "").lower() in want]
    food = [u for u in fit if u.get("kind") == "food" and u.get("expires_at")]
    if food and len(food) == len(fit):          # v2.1: food by the buyer's shelf-life need (v2.1a: food-only fits)
        need = stated_need_hours(text, [u.get("name") for u in food])
        mfr = ((pol or {}).get("shop") or {}).get("food_min_left_frac")   # v2.10
        if need is None:
            food = [u for u in food if u.get("sku") != "Gb375f74f" or P.hours_left(u, now) >= 28.8]
        u = pick_food(food, need, shelf or shelf_hours, now, min_frac=mfr)
        if u is None and why is not None and need is not None:
            why["need_h"] = need
        elif u is None and why is not None:
            why["short_shelf"] = mfr
        return u
    # goods: ship the unit that expires first among those whose grade is A/B; else the first to expire.
    good = [u for u in fit if u.get("grade") in ("A", "B")]
    pool = good or fit
    # v2.11 (11-10, book): in shop.c_first_leaves, a request that names no grade gets grade C first. All 18 C
    # furniture SKUs also had A/B in stock, so C was almost never offered (sofa C 8, bed C 14 sales; 118 units unsold).
    cfl = ((pol or {}).get("shop") or {}).get("c_first_leaves") or ()
    cs = [u for u in fit if u.get("grade") == "C" and P.leaf(u.get("category")) in cfl]
    if cs and not GRADE_WORD_RE.search(text):
        pool = cs
    if stats is not None and pol is not None:   # v2.6: a unit the market price can reach first
        pool = [u for u in pool if sells_at_market(u, pol, stats, inv, now)] or pool
    return min(pool, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0), default=None)


def refused_units(path=None, now=None, hold_s=REFUSED_HOLD_S, tail=KIT_TAIL_BYTES):
    """v2.3: units the kit refused at checkout ("unit X is not in stock for this order") in the last hold_s, read
    from the tail of the kit log. The kit keeps a unit out of a new checkout while an earlier session on it waits
    for payment (market_kit.py pick_unit `held`), and keeps sessions until 500 newer ones exist, so a unit whose
    buyer walked away is refused for hours (24 h to 11-07 12:30 KST: 305 refusals on 25 units, one unit 81 times
    over 6.3 h). Log lines carry the local time of day only: a time later than now is from the day before."""
    now = time.time() if now is None else now
    try:
        with open(KIT_LOG if path is None else path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return set()
    lt = time.localtime(now)
    day0 = now - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
    out = set()
    for line in data.splitlines():
        m = REFUSED_RE.match(line)
        if not m:
            continue
        t = day0 + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if t > now + 60:
            t -= 86400
        if now - t <= hold_s:
            out.add(m[4])
    return out


WARRANTY_RE = re.compile(r"warrant|보증", re.I)
GRADE_WORD_RE = re.compile(r"\bgrade\b|등급", re.I)   # v2.11: a request that names a grade keeps A/B first


def warranty_on_request(text, unit, pol, stats, price, floor):
    """v2.4 (2026-11-07, stars): goods warranty hours when the request mentions a warranty and the price still
    clears the floor after the expected refunds (price x (1 - fail rate) >= floor); else 0. Policy warranty_hours
    turns a warranty on only when warranty_uplift beats the fail rate, so beds and sofas (fail 6.7 to 6.8% in 72 h)
    went out without one, and one buyer whose requests ask about warranties marked them down 2 stars each ("No
    warranty, so 2 stars off"; its 11-07 12시 bed deals averaged 2.9). Hours and fail rate as in
    policy.warranty_hours."""
    if unit.get("kind") != "goods" or not WARRANTY_RE.search(text or ""):
        return 0
    c = P.resolve(pol, unit.get("category"), "goods")
    hours = min(int((c.get("warranty_hours") or {}).get(unit.get("grade"), 0)), int(c.get("max_warranty_hours", 6)))
    s = P._stat(stats, "category", unit.get("category"))
    fail = s["fail_rate"] if s and s.get("n", 0) >= 10 and s.get("fail_rate") is not None else c.get("prior_fail_rate", 0.05)
    return hours if hours > 0 and price * (1 - fail) >= floor else 0


CARD_DESCRIPTION = "Signed receipts with certificate; grades never overstated. Ask by item or SKU."
SKILL_DESCRIPTION = "Ask for an item by name or SKU, then send a whole-number price."


def opening_text(unit, pol, ask, stats=None, shelf_hours=None, now=None):
    """v1.8: the first reply states exactly what the receipt would claim at this moment (P.receipt_terms: the grade,
    or none when a food grade cut is within claim_window_s or the grade is unknown), shelf life for food, and the
    ask as the last number of the text (readers of the last number, like our offer_in, get the price). The
    certificate is on the card (every receipt has cert); no warranty words (receipt_terms decides it per price)."""
    t = P.receipt_terms(unit, pol, stats=stats, price=ask, now=now, shelf_hours=shelf_hours)
    g = t.get("claim")
    gtxt = f"grade {g}" if g in ("A", "B", "C") else "grade not stated"
    h = P.hours_left(unit, now)
    life = f", {int(h)} h shelf life left" if unit.get("kind") == "food" and h is not None else ""
    w = t.get("warranty_hours")
    wtxt = f", {w} h warranty" if w else ""
    return f"{unit['name']} ({unit['sku']}), {gtxt}{life}{wtxt}: asking {ask}. Offer a whole number."


def receipt_item(unit, text):
    """Echo what the buyer asked for. Request contains the unit's SKU, or names no
    category -> the SKU (unchanged). Request names the unit's category leaf but not its SKU -> that leaf, which is the
    R3 order item (buyer kit signs item == order item; our ACP pick_unit accepts leaf, unit pinned by the terms).
    Seen in the ledger: q00 rates SKU receipts "SCAM" and other shops echo the category to it. Never raises."""
    sku = unit.get("sku")
    try:
        t = text if isinstance(text, str) else ""
        if sku and sku in t:
            return sku
        leaf = str(unit.get("category") or "").split(">")[-1].strip()
        if leaf and leaf in t:
            return leaf
    except Exception:  # noqa: BLE001
        pass
    return sku


def offer_in(text):
    t = SKU_RE.sub(" ", text)
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", t)
    if not nums:
        return None
    try:
        return int(float(nums[-1].replace(",", "")))
    except ValueError:
        return None


def shelf_hours(sku):
    cat = read_json(MARKET_DIR / "catalog.json", {}) or {}
    if isinstance(cat, list):
        cat = {x["sku"]: x for x in cat}
    return (cat.get(sku) or {}).get("shelf_hours")


# LR 2026-11-17: bounded small-loss rescue of would-be rejects (see Seller._loss_rescue)
LR_DEFAULT = {"enabled": True, "kinds": ["food"], "min_offer_frac": 0.7, "max_loss_abs": 5000, "max_loss_per_h": 60000}
LR_OFF = str(Path(__file__).resolve().parent.parent / ".market" / "loss_rescue_off")
FA_OFF = str(Path(__file__).resolve().parent.parent / ".market" / "final_accept_off")
FA_MARGIN_PERMILLE = 120   # FA: accept goods offers >= cost x 1.12 at the reject point
LR_LOG = str(Path(__file__).resolve().parent.parent / ".market" / "loss_rescue.jsonl")   # durable 1 h loss window

class Seller(AgentExecutor):
    def __init__(self, pol=None):
        self.pol = pol or P.load()
        # v1.16: re-read policy.json when its hash changes (no kit restart); a given pol (tests) is never replaced,
        # R3_POLICY_RELOAD=0 turns it off
        self._pw = (P.PolicyWatcher(pol=self.pol) if pol is None and os.environ.get("R3_POLICY_RELOAD", "1") != "0"
                    else None)
        self.open = {}
        self.accepted = {}                              # v2.3: unit -> time we signed its receipt
        self._shelf = {}
        self._vol = (0.0, 0.0, 0)
        self._R, self._rerr, self._rt, self.checkpoints = None, None, 0.0, 0
        self._lock = threading.Lock()                   # Resident is used by A2A calls and the tick thread
        self._ticker = None
        self._blog = {}                                 # v1.20 logging only: task id -> (buyer_ref, source)
        self.arms_policy_path = self._pw.path if self._pw is not None else getattr(P, "POLICY_FILE", None)  # v2.12

    def held(self, now):
        """v2.3: units not to offer now: in an open negotiation, sold in the last ACCEPT_HOLD_S (two buyers on one
        unit, the second checkout refused: 19 units in 24 h), or refused at checkout in the last REFUSED_HOLD_S."""
        for k in [k for k, t in self.accepted.items() if now - t > ACCEPT_HOLD_S]:
            del self.accepted[k]
        return {d["unit"]["unit"] for d in self.open.values()} | set(self.accepted) | refused_units(now=now)

    def _bl(self, event, tid, **kw):
        """v1.20 logging only; never raises, never changes a decision."""
        try:
            b, src = self._blog.get(tid) or ("unknown", "none")
            BL.write(event, b, src, tid, **kw)
        except Exception:                               # noqa: BLE001
            pass

    def refresh_policy(self, now=None):
        """v1.16: switch to a changed policy.json between deals; on a bad file keep the current one and warn (stderr
        + metric policy_reload_failed). Open deals keep the policy they were opened with (deal["pol"])."""
        if LIVE is not None:                             # LLM flags re-read here (reload, no restart)
            _on, _chg = LIVE.refresh()
            if _chg:
                print(f"INFO: seller: llm live {"ON" if _on else "OFF"}", file=sys.stderr, flush=True)
                self._metric("llm_live", {"on": _on})
        if self._pw is None:
            return self.pol
        try:
            pol, ev = self._pw.get(now)
        except Exception as e:                           # noqa: BLE001
            pol, ev = self.pol, ("reload_failed", None, f"{type(e).__name__}: {str(e)[:150]}")
        if ev and ev[0] == "reload":
            self.pol = pol
            self._vol = (0.0, 0.0, 0)                    # volume level from the new policy at the next deal
            print(f"INFO: seller: policy reloaded {ev[1]} -> {ev[2]}", file=sys.stderr, flush=True)
            self._metric("policy_reload", {"old": ev[1], "new": ev[2]})
        elif ev:
            print(f"WARNING: seller: policy.json {ev[1]} not loaded ({ev[2]}); keeping {self.pol['_hash']}",
                  file=sys.stderr, flush=True)
            self._metric("policy_reload_failed", {"hash": ev[1], "error": ev[2], "kept": self.pol["_hash"]})
        return self.pol

    def stats(self):
        """Policy stats from the in-process aggregates.Resident (finding d + 09:32 wiring): opened once (one
        snapshot load), refresh() = only new ledger events, called per new deal. Any failure -> None (policy
        priors) and a retry of the open after 60 s; the error goes to the metric (stats_error)."""
        if self._R is None:
            if time.time() - self._rt < 60:
                return None
            self._rt = time.time()
            cat = read_json(MARKET_DIR / "catalog.json", {}) or {}
            self._R, self._rerr = open_resident({x["sku"]: x for x in cat} if isinstance(cat, list) else cat)
            if self._R is None:
                return None
            atexit.register(self.close_stats)
            if TICK_S > 0:
                self._ticker = threading.Thread(target=self._tick_loop, name="stats-tick", daemon=True)
                self._ticker.start()
        try:
            with self._lock:
                self._R.refresh()
            return self._R.A
        except Exception as e:                  # noqa: BLE001
            self._R, self._rerr = None, f"{type(e).__name__}: {str(e)[:150]}"
            return None

    def tick(self):
        """Refresh + checkpoint-if-due without a deal: the shop ledger only grows when restock ingests (60 s), so
        without this a quiet seller would never checkpoint and restock's StatsView would stay empty."""
        self.after_reply(refresh=True)

    def _tick_loop(self):
        while self._R is not None:
            time.sleep(TICK_S)
            self.tick()

    def after_reply(self, refresh=False):
        """Checkpoint (snapshot + StatsView shards) when 500 events or 30 s are due; never fails the task."""
        try:
            with self._lock:
                if self._R is not None and refresh:
                    self._R.refresh()
                if self._R is not None and self._R.maybe_checkpoint():
                    self.checkpoints += 1
        except Exception as e:                  # noqa: BLE001
            self._rerr = f"checkpoint {type(e).__name__}: {str(e)[:140]}"

    def close_stats(self):
        try:
            with self._lock:
                if self._R is not None:
                    self._R.close()
        except Exception:                       # noqa: BLE001
            pass

    def volume(self):
        """Min-volume relax level for the shop side, re-read at most every 30 s."""
        if time.time() - self._vol[0] > 30:
            self._vol = (time.time(), *volume_level("shop", self.pol))
        return self._vol[1], self._vol[2]

    def shelf(self, sku):
        if sku not in self._shelf:
            self._shelf[sku] = shelf_hours(sku)
        return self._shelf[sku]

    def decide(self, deal, offer):
        """Policy move for a buyer offer, behind the hard guard. A PolicyError/TypeError (price not strictly above
        cost, not an int) is never a task error: counter at the floor (or reject after 8 rounds), metric guard_refused."""
        u = deal["unit"]
        pol = deal.get("pol") or self.pol                # v1.16: the policy the deal was opened with
        try:
            hist = deal.get("offers") or []             # v1.6: the buyer's earlier offers (patience, stall, cap)
            mk = P.market_floor(u, pol, deal.get("stats"), deal["ask"])   # v2.7: shop.market_floor_frac
            move, price = P.respond(u, pol, deal["ask"], offer, deal["rounds"], deal["inv"], relax=deal["relax"],
                                    prev_offer=hist[-1] if hist else None, history=list(hist), market=mk)
            if move == "accept":
                price = P.guard_price(u, price)                 # hard guard: strictly above cost, int only
            elif move == "reject":
                fa = self._final_accept(u, offer)              # FA 11-17: profitable offer refused only by the market floor
                if fa is not None:
                    return "accept", fa
                lr = self._loss_rescue(u, pol, offer, deal)     # LR 11-17: small bounded loss instead of a no-deal
                if lr is not None:
                    return "accept", lr
            return move, price
        except (P.PolicyError, TypeError) as e:
            floor = P.floor_price(u, pol, deal["inv"], relax=deal["relax"])
            self._metric("guard_refused", {"sku": u["sku"], "unit": u["unit"], "offer": offer, "floor": floor,
                                           "error": str(e)[:120]}, pol=pol)
            return ("counter", floor) if deal["rounds"] < 8 else ("reject", None)

    def _final_accept(self, u, offer):
        """FA 2026-11-17: at the point respond() REJECTS a goods offer (last round, buyer not rising), accept when the
        offer is >= ceil(cost x (1 + FA_MARGIN)). respond() can reject such offers because the v2.7 market floor
        (0.9 x SKU median) or the grade floor sits above them (6 h: e.g. bed cost 588,270 offer 1,010,981 rejected).
        The alternative is no sale: with < 9 h left and ~77 h of furniture cover, the unit stays at cost in Book, so a
        sale at >= cost x 1.12 raises Book (12% covers the ~6.9% bed breakage refund rate) and avoids a no-deal
        review. Flag file FA_OFF disables it; any error -> None (the reject stands)."""
        try:
            if os.path.exists(FA_OFF) or offer is None or u.get("kind") != "goods":
                return None
            off = P.as_int_price(offer)
            cost = int(u["cost"])
            need = -(-cost * (1000 + FA_MARGIN_PERMILLE) // 1000)
            if off < need:
                return None
            price = P.guard_price(u, off)
            self._metric("final_accept", {"sku": u.get("sku"), "unit": u.get("unit"), "cost": cost, "offer": off,
                                          "margin": round(off / cost - 1, 4)})
            return price
        except Exception:                                # noqa: BLE001 - any doubt: the reject stands
            return None

    def _loss_rescue(self, u, pol, offer, deal):
        """LR 2026-11-17 (owner t920u/t921u seller push): where respond() would REJECT (last round, buyer not
        rising), accept the buyer's offer instead when the loss is small and bounded. A rejected buyer that committed
        to us leaves a 'no deal' review (2.9 stars avg, 6 h); a paid deal averages 4.66. Policy shop.loss_rescue:
        {enabled, kinds [food], min_offer_frac 0.7 (offer >= ceil(cost x frac)), max_loss_abs 5000 (cost - offer),
        max_loss_per_h 60000 (rolling 1 h sum of cost - offer over rescues)}; no policy key -> LR_DEFAULT (same values).
        Disabled key, the flag file LR_OFF, or any error -> None (the reject stands). Undo: touch .market/loss_rescue_off."""
        try:
            if os.path.exists(LR_OFF):                   # instant undo: touch .market/loss_rescue_off
                return None
            cfg = (pol.get("shop") or {}).get("loss_rescue") or LR_DEFAULT
            if not cfg.get("enabled") or offer is None:
                return None
            if u.get("kind") not in (cfg.get("kinds") or ["food"]):
                return None
            off = P.as_int_price(offer)
            cost = int(u["cost"])
            frac = float(cfg.get("min_offer_frac", 0.7))
            if off < 1 or off * 1000 < cost * int(round(frac * 1000)):
                return None
            loss = cost - off
            if loss > int(cfg.get("max_loss_abs", 5000)):
                return None
            now = time.time()
            win = []                                     # v2 (Codex): durable window in LR_LOG, survives restarts
            try:
                with open(LR_LOG) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        d = json.loads(line)              # v3 (Codex): a malformed record -> exception -> no rescue
                        if now - float(d["ts"]) < 3600:
                            win.append((float(d["ts"]), int(d["loss"])))
            except FileNotFoundError:
                pass
            if loss > 0 and sum(x[1] for x in win) + loss > int(cfg.get("max_loss_per_h", 60000)):
                return None
            if loss > 0:                                 # record BEFORE accepting; no durable record -> no rescue
                with open(LR_LOG, "a") as f:              # leading newline: a torn trailing record cannot absorb this one
                    f.write("\n" + json.dumps({"ts": now, "loss": loss, "unit": u.get("unit")}) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                win.append((now, loss))
            self._metric("loss_rescue", {"sku": u.get("sku"), "unit": u.get("unit"), "cost": cost, "offer": off,
                                         "loss": loss, "rounds": deal.get("rounds"),
                                         "loss_1h": sum(x[1] for x in win)}, pol=pol)
            return off
        except Exception:                                # noqa: BLE001 - any doubt: the reject stands
            return None

    def deal_policy(self, unit, now):
        """v2.12: (policy, picked arms) for a new sale of `unit`: P.with_arms over the live arms of policy_arms.json,
        salted by the unit id (P.arm_salt; the market settle carries the unit), so a unit offered again in a later task
        keeps its arm. The pick (find_unit) and decline_texts already ran on self.pol. No arms or any error ->
        (self.pol, {}), the policy object itself."""
        try:
            arms = P.load_arms(self.arms_policy_path, now) if self.arms_policy_path else None
            if not arms:
                return self.pol, {}
            return P.with_arms(self.pol, arms, P.arm_salt(unit["unit"]))
        except Exception as e:                           # noqa: BLE001 - the unchanged policy on any error
            print(f"WARNING: seller: arms skipped ({type(e).__name__}: {str(e)[:150]})", file=sys.stderr, flush=True)
            return self.pol, {}

    def _metric(self, decision, outcome, pol=None):
        """pol: the deal's own policy (v1.16 reload: a deal opened before a reload is tagged with its own hash)."""
        r = Recorder("seller", policy_hash=(pol or self.pol)["_hash"])
        r.write(decision, Ledger().last_seq(), outcome=outcome)

    async def execute(self, context, event_queue):
        try:
            await self._execute(context, event_queue)
        finally:
            self.after_reply()                  # after the answer is queued: checkpoint if due

    async def _execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now = time.time()
        for tid in [k for k, d in self.open.items() if now - d["t"] > HOLD_S and k != task.id]:
            _d = self.open[tid]
            del self.open[tid]
            self._bl("outcome", tid, outcome="no_response", sku=_d["unit"].get("sku"), last_ask=_d.get("ask"),
                     rounds=_d.get("rounds"), idle_s=round(now - _d["t"], 1))
            self._blog.pop(tid, None)
        if A2 is not None:                        # model trade switch: <W>/a2/LIVE, read per request
            _a2 = A2.bridge(os.getcwd(), MARKET_DIR, sign=sign, name=NAME, receipt_terms=P.receipt_terms,
                            offer_in=offer_in, opening_text=opening_text, metric=self._metric, ledger=Ledger,
                            policy=self.refresh_policy, shelf=self.shelf, data_part=new_data_part,
                            a1_held=lambda: {d["unit"]["unit"] for d in self.open.values()})
            if _a2.wants(task.id, task.id in self.open) and await _a2.handle(task.id, text, up, say):
                return
        deal = self.open.get(task.id)
        if deal is None:
            try:
                self._blog[task.id] = BL.buyer_ref(context)
            except Exception:                           # noqa: BLE001
                self._blog[task.id] = ("unknown", "none")
            self._bl("request", task.id, text=BL.clean(text, 200))   # v121c: masked
            self.refresh_policy()                       # v1.16: only between deals
            if declined_phrase(text, self.pol):         # v2.2: shop.decline_texts, before any stock is read
                self._metric("declined", {"request": BL.clean(text, 80)})
                self._bl("outcome", task.id, outcome="declined", request=BL.clean(text, 80))
                self._blog.pop(task.id, None)
                await up.reject(say("Sorry, we can't take this order."))
                return
            inv = read_json(MARKET_DIR / "inventory.json", {}) or {}
            why = {}
            st = self.stats()                           # v2.6: before the pick (sells_at_market)
            unit = find_unit(text, inv, self.held(now), self.pol, shelf=self.shelf,
                             why=why, stats=st)
            if unit is None:
                need = why.get("need_h")                # v2.1: food in stock, none lasts the stated need
                self._metric("no_stock", dict({"request": BL.clean(text, 80)}, **why))
                self._bl("outcome", task.id, outcome="no_stock", request=BL.clean(text, 80), **why)
                self._blog.pop(task.id, None)
                await up.reject(say("Sorry, we have none of that in stock." if need is None
                                    else f"Sorry, none of our units has {need:g} h of shelf life left."))
                return
            pol, arms = self.deal_policy(unit, now)     # v2.12: arms by unit, this deal's policy for every round
            sh = self.shelf(unit["sku"])
            relax, nset = self.volume()
            ask = P.ask_price(unit, pol, stats=st, inv=inv, shelf_hours=sh, relax=relax)
            deal = self.open[task.id] = {"unit": unit, "ask": ask, "rounds": 0, "t": time.time(), "inv": inv, "sh": sh, "text": text,
                                         "relax": relax, "stats": st, "pol": pol, "arms": arms}
            self._metric("open", {"sku": unit["sku"], "unit": unit["unit"], "cost": unit["cost"], "ask": ask,
                                  "floor": P.floor_price(unit, pol, inv, relax=relax), "relax": relax,
                                  "settled": nset, "stats": st is not None, "stats_error": self._rerr,
                                  "stats_seq": getattr(st, "s", {}).get("seq") if st is not None else None,
                                  **({"arms": arms} if arms else {})})
            self._bl("quote", task.id, kind="open", sku=unit["sku"], unit=unit["unit"], category=unit.get("category"),
                     grade=unit.get("grade"), ask=ask, cost=unit.get("cost"), policy_hash=pol.get("_hash"))
            await up.requires_input(say(opening_text(unit, pol, ask, st, sh)))
            return
        deal["rounds"] += 1
        deal["t"] = now
        offer = offer_in(text)
        if offer is None and LIVE is not None and LIVE.on:   # LLM interpreter assist, rule first, 8 s
            offer, _src = await LIVE.offer(text, offer_in, task.id, deal["ask"])
            self._metric("llm_parse", {"src": _src, "got": offer is not None})
        u = deal["unit"]
        move, price = self.decide(deal, offer)
        self._bl("offer", task.id, sku=u["sku"], offer=offer, round=deal["rounds"], prev_ask=deal["ask"], move=move,
                 price=price, **BL.offer_fields(text))   # first 80 chars, secrets masked
        if offer is not None:
            deal.setdefault("offers", []).append(offer)  # v1.5/v1.6: next respond's prev_offer/history (pinned by tests_lead/test_v16_wiring.py)
        if move == "accept":
            terms = P.receipt_terms(u, deal.get("pol") or self.pol, stats=deal.get("stats"), price=price, shelf_hours=deal["sh"])
            if not terms.get("warranty_hours"):         # v2.4: warranty when the request asks about one
                w = warranty_on_request(deal.get("text"), u, deal.get("pol") or self.pol, deal.get("stats"), price,
                                        P.floor_price(u, deal.get("pol") or self.pol, deal["inv"], relax=deal["relax"]))
                if w:
                    terms["warranty_hours"] = w
            receipt = dict({"shop": NAME, "item": receipt_item(u, deal.get("text")), "price": price, "task": task.id}, **terms)
            receipt = sign(receipt)
            self.accepted[u["unit"]] = time.time()
            try:                                        # v1.3: cost layer for this unit before its sale is ingested
                with Ledger() as L:
                    L.first_seen_unit(u)
            except Exception as e:                      # noqa: BLE001  (never fail a sale on bookkeeping)
                self._metric("ledger_error", {"unit": u.get("unit"), "error": str(e)[:120]}, pol=deal.get("pol"))
            self._metric("accept", {"sku": u["sku"], "unit": u["unit"], "cost": u["cost"], "price": price,
                                    "below_cost": price < u["cost"], "rounds": deal["rounds"],
                                    "relax": deal["relax"], "claim": receipt.get("claim"),
                                    "warranty_hours": receipt.get("warranty_hours", 0),
                                    **({"arms": deal["arms"]} if deal.get("arms") else {})}, pol=deal.get("pol"))
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            self._bl("outcome", task.id, outcome="accept", sku=u["sku"], price=price, rounds=deal["rounds"])
            self._blog.pop(task.id, None)
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            del self.open[task.id]
            return
        if move == "reject":
            self._metric("reject", {"sku": u["sku"], "offer": offer, "rounds": deal["rounds"]}, pol=deal.get("pol"))
            self._bl("outcome", task.id, outcome="reject", sku=u["sku"], offer=offer, rounds=deal["rounds"],
                     last_ask=deal["ask"])
            self._blog.pop(task.id, None)
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        deal["ask"] = price
        self._bl("quote", task.id, kind="counter", sku=u["sku"], ask=price, round=deal["rounds"])
        await up.requires_input(say(f"Counteroffer: {price}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task is None:
            return
        if A2 is not None:                        # free the unit a model-seller deal held
            A2.cancel(task.id)
        _had = self.open.pop(task.id, None)
        self._bl("outcome", task.id, outcome="cancel", open=_had is not None)
        self._blog.pop(task.id, None)
        self._metric("cancel", {"task": task.id})
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


try:                                              # v120c-B: default OFF; any import/config error -> no LLM
    from llm.live_v120c import Live as _Live
    LIVE = _Live()                                # OFF unless flags; refresh() re-reads them on policy reload
except Exception:                                 # noqa: BLE001
    LIVE = None


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME, description=CARD_DESCRIPTION,
        version="3.1.0", default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description=SKILL_DESCRIPTION, tags=["shopping"],
                           examples=["I want to buy 종가 포기김치 10kg (G00001)", "52000"])])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
