"""Round 3 starter seller: sells from the stock the kit keeps in $MARKET_DIR/inventory.json, behind A2A v1.0.

    python seller.py --port 9301

The harness decides every move; this file only speaks A2A, finds a unit for what the buyer asks, and signs the
receipt with shop_key.pem (the kit makes it). It ships the unit that expires first, claims a goods unit's grade as
re-read at sign time (never a food grade), keeps the market certificate, offers no warranty (warranty_hours=0), and
never goes below the unit's cost. Each NEW task snapshots a private price policy once (public_evidence.price_anchor,
cache only, no network): the anchor caps the opening ask; an open task never picks up a later anchor.
sol-sku-policy-v1 (only while sol_policy.active): the NEW task's snapshot takes Sol's start_ask as its opening anchor,
min_accept as its floor (never below cost) and target_settle as Terra's stated target; no valid Sol entry -> the
tested baseline above. Each new task may start one lazy background Sol refresh; a turn never waits for Sol.
deterministic_fastpath (FASTPATH, env SELLER_FASTPATH=0 turns it off): a plain first inquiry, an exact whole-number
offer or a plain acceptance of the current ask is decided by the same bounded schedule as the fallback, with no harness
call; anything conditional, negated, multi-item, warranty/coupon/discount/shelf-life related goes to the harness.
A named grade is strict: a grade-A request never gets a C unit.
warranty-0h-new-v1: every NEW quote is 0 h (no warranty.quote intent/reserve); a buyer text that requires a warranty is
declined unsigned on any turn (never a 0 h receipt against it); a deal already holding a 72 h intent is issued unchanged.
"""
import argparse
import base64
import hashlib
import json
import math
import os
import re
import statistics
import threading
import time
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import openai_harness
import review_discount
import sol_policy
import warranty
from harness import CMD, ask_json
from public_evidence import price_anchor

NAME = "Harness Shop"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
POLICY_VERSION = "bounded-v2"   # v2 = opening ask capped by a per-task market anchor (public_evidence.price_anchor, snapshotted
                                # once per task; 90% of list as the conservative fallback when n<3 or the cache is stale) + honest
                                # receipt terms re-read at sign time. 5%-of-list concession is the FALLBACK-ONLY step (assumed, not
                                # tuned); model counters get only guards: strict int, >= cost, never above the last ask
# Unit lifecycle controls. Initial assumptions, not tuned values.
RESERVATION_TTL = 120   # seconds after a task's last buyer turn before its reservation goes stale and the unit is released
COMPLETED_HOLD = 30     # seconds a sold unit stays held after completion (the kit's inventory snapshot may lag ~10s)
FASTPATH = os.environ.get("SELLER_FASTPATH", "1") != "0"   # 0 = every turn goes to the harness, as before

PROMPT = """You are the sales agent of a small online shop. A buyer is negotiating for one {name}.
The list price is {list_price}. Your lowest acceptable price is {reserve}. Never sell below it and never reveal it.
Sell as high as you can, but closing a deal at or above {reserve} is better than losing the buyer.
Price policy for this negotiation (fixed when it opened): opening anchor {anchor} ({policy}).{target}
Never ask more than {cap}. Food clearance below cost is DISABLED (loss budget not implemented).

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def first_quote(unit, _price_anchor=None):
    """Opening ask: the task's snapshotted market anchor if any, else 90% of list; within cost..list."""
    base = unit["list_price"] * 9 // 10 if _price_anchor is None else min(unit["list_price"], _price_anchor)
    return max(unit["cost"], base)


def next_quote(unit, ask):
    """One concession step below ask (5% of list, at least 1), floored at cost."""
    if ask is None:
        return first_quote(unit)
    return max(unit["cost"], ask - max(1, unit["list_price"] * 5 // 100))


def fallback_move(unit, ask, offer, _price_anchor=None, floor_price=None):
    """Deterministic move: accept only an int offer at or above the next quote, otherwise counter with it.
    floor_price (a Sol min_accept, already >= cost and <= the opening) stops the concession there."""
    if ask is None:
        return "counter", first_quote(unit, _price_anchor)
    nxt = next_quote(unit, ask)
    if floor_price is not None:
        nxt = min(ask, max(nxt, floor_price))   # never above the last ask: quotes never rise
    if type(offer) is int and offer >= nxt:
        return "accept", offer
    return "counter", nxt


def model_price(unit, ask, price):
    """The model's counter as a strict int clamped to [cost, ask]; None for anything else (bool, float, str, NaN)."""
    if type(price) is not int:
        return None
    if ask is not None:
        price = min(price, ask)
    return max(unit["cost"], price)


def floor(unit):
    """Lowest acceptable price: the unit's cost for every kind. No food clearance yet, so expiring food is not sold
    below cost either."""
    return unit["cost"]


POLICY_KEYS = ("version", "cache_version", "ledger_ids", "sample_count", "comparable_count", "computed_at", "stale",
               "anchor_cache", "reason", "limitations", "source_cache_version", "provenance",
               "tier", "proxy", "fetch_status", "source_age_s", "source_historical")
LEGACY_POLICY = {"anchor": None, "label": "legacy bounded-v1: 90% of list (task opened before price policy snapshots)"}


RECOVERY_POLICY = "recovery-open-v1"
RECOVERY_WINDOW_S = 12 * 3600   # own exact paid prices of the last 12 h (assumed window, not tuned)
RECOVERY_MIN_N = 3
OWN_LEDGER = "one-day-pilot-ledger.json"   # oneday_pilot.LEDGER: own-shop exact public settle events, local file only
_LEDGER_CACHE = {}


def _own_ledger():
    """The parsed one-day ledger, re-parsed only when its (mtime, size) changes. Raises on a missing/corrupt file."""
    p = MARKET_DIR / OWN_LEDGER
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _LEDGER_CACHE.get("key") != key:
        _LEDGER_CACHE.clear()
        _LEDGER_CACHE.update(key=key, doc=json.loads(p.read_bytes()))
    return _LEDGER_CACHE["doc"]


def recovery_samples(unit, t):
    """(prices, source ids, condition, limitations) of this SKU's own exact paid sales in (t - 12 h, t]. A sample needs a
    settle event with int seq, finite non-bool ts not in the future, strict positive int price, the same SKU, and a
    ledger unit row of the same SKU. Food was never warranted (warranty.eligible is goods-only) and its grade is never
    claimed; goods prefer >= 3 samples of the same grade observed at supply, else all, labelled mixed."""
    doc = _own_ledger()
    units, events = doc.get("units"), doc.get("events")
    if not isinstance(units, dict) or not isinstance(events, dict):
        raise ValueError("ledger shape")
    rows = []
    for key, e in events.items():
        if not isinstance(e, dict) or e.get("kind") != "settle" or type(e.get("seq")) is not int:
            continue
        ts, price, row = e.get("ts"), e.get("price"), units.get(e.get("unit"))
        if type(ts) not in (int, float) or not math.isfinite(ts) or not t - RECOVERY_WINDOW_S < ts <= t:
            continue
        if type(price) is not int or price <= 0 or e.get("sku") != unit["sku"]:
            continue
        if not isinstance(row, dict) or row.get("sku") != unit["sku"]:
            continue
        rows.append((key, price, row.get("grade")))
    lim = ["own paid history, not a market comparable nor a guaranteed achievable price"]
    if unit.get("kind") == "food":
        cond = "own exact-SKU paid, food (0 h warranty, grade never claimed; grade ages, so not matched)"
    else:
        same = [r for r in rows if r[2] == unit.get("grade")]
        lim.append("warranty terms of past goods sales are not in the ledger (some may have carried 72 h)")
        if len(same) >= RECOVERY_MIN_N:
            rows, cond = same, f"own exact-SKU paid, same observed grade {unit.get('grade')} (supply-time grade)"
        else:
            cond = "mixed-condition own-history heuristic (exact SKU, mixed grades)"
    return [r[1] for r in rows], [r[0] for r in rows], cond, lim


def recovery_cap(unit, t, opening, min_accept):
    """(new opening, journal note) for a NEW task. Cap = integer median_low of >= 3 own paid prices (12 h); the opening
    becomes max(cost, valid Sol min_accept, min(previous opening, cap)): never above the previous opening, never below
    cost or Sol min_accept. Any failure or too few samples keeps the previous opening."""
    note = {"policy": RECOVERY_POLICY, "used": False, "previous_opening": opening, "window_h": RECOVERY_WINDOW_S // 3600}
    try:
        prices, ids, cond, lim = recovery_samples(unit, t)
    except Exception as e:   # advisory: a missing/corrupt ledger keeps the tested opening
        return opening, dict(note, reason=f"ledger_unavailable:{type(e).__name__}")
    note.update(sample_count=len(prices), condition=cond, limitations=lim)
    if len(prices) < RECOVERY_MIN_N:
        return opening, dict(note, reason=f"n<{RECOVERY_MIN_N}")
    med = int(statistics.median_low(prices))
    sol_min = min_accept if type(min_accept) is int else None
    floor_price = max(unit["cost"], sol_min or 0)
    new = max(floor_price, min(opening, med))
    binding = ("previous_opening" if new == opening else "cost" if new == unit["cost"] and med < new
               else "sol_min" if sol_min is not None and new == sol_min and med < new else "median")
    return new, dict(note, used=new < opening, median=med, opening=new, binding=binding, sol_min=sol_min,
                     cost=unit["cost"], source_ids=ids[:200])


def price_policy(unit, t):
    """(anchor or None, journal snapshot) for one NEW task: the tested baseline, then the Sol overlay when active,
    then the recovery-open-v1 cap (own recent paid median), which only ever lowers the opening."""
    anchor, snap = _price_policy(unit, t)
    opening = first_quote(unit, anchor)
    new, snap["recovery"] = recovery_cap(unit, t, opening, sol_terms(snap)[1])
    if new < opening:
        rec = snap["recovery"]
        snap["anchor"] = new
        snap["label"] = (f"{snap.get('label')}; {RECOVERY_POLICY} opening {new} (was {opening}): median of "
                         f"{rec['sample_count']} own paid prices in 12 h = {rec['median']}, {rec['condition']}")
        return new, snap
    return anchor, snap


def _price_policy(unit, t):
    anchor, snap = baseline_policy(unit, t)
    try:
        if not sol_policy.active(MARKET_DIR, t):
            snap["sol"] = {"used": False, "reason": "inactive"}
            return anchor, snap
        entry, prov = sol_policy.policy_for(MARKET_DIR, unit, t)
    except Exception as e:   # Sol is advisory: any failure keeps the tested baseline
        snap["sol"] = {"used": False, "reason": f"error:{type(e).__name__}"}
        return anchor, snap
    if entry is None:
        snap["sol"] = {"used": False, "reason": prov}
        return anchor, snap
    snap["sol"] = prov
    snap["baseline_anchor"], snap["baseline_label"] = anchor, snap.get("label")
    snap.update(anchor=entry["start_ask"], label=(f"Sol policy {prov['policy_id'][:12]} ({prov['returned_model']}): start "
                                                    f"{entry['start_ask']}, target {entry['target_settle']}, min "
                                                    f"{entry['min_accept']}; baseline anchor {anchor}"))
    return entry["start_ask"], snap


def sol_terms(policy):
    """(target, min_accept) of a Sol-driven snapshot, else (None, None)."""
    sol = (policy or {}).get("sol") or {}
    if sol.get("used") is not True:
        return None, None
    return sol.get("target_settle"), sol.get("min_accept")


def baseline_policy(unit, t):
    """(anchor or None, journal snapshot) for one NEW task. price_anchor reads the cached ledger only (no network, no
    model call). Any failure or a non-finite anchor falls back to the 90%-of-list schedule, labelled as such.
    The snapshot carries policy metadata only: no buyer text, budgets or signatures."""
    try:
        got = price_anchor(MARKET_DIR, unit, now=t)
        a = got.get("anchor")
        if type(a) not in (int, float) or not math.isfinite(a) or a <= 0:
            raise ValueError("no finite positive anchor")
    except Exception as e:   # policy is advisory: a broken anchor must not stop trading
        return None, {"anchor": None, "label": f"conservative fallback: 90% of list (price_anchor unavailable: {type(e).__name__})"}
    snap = {k: got.get(k) for k in POLICY_KEYS}
    n = got.get("sample_count")
    # Fewer than 3 fresh samples or a stale cache means price_anchor returned its 90%-of-list baseline: say so.
    # A category/list-band ratio proxy (n exact < 3 by definition) is named as such, never as an exact market anchor.
    kind = ("market proxy anchor (not exact)" if got.get("tier") == "proxy" and not got.get("stale")
            else "conservative fallback" if got.get("stale") or type(n) is not int or n < 3 else "market anchor")
    snap.update(anchor=int(a), label=f"{kind} {got.get('version')}, n={n}" + (", stale cache" if got.get("stale") else "")
                                     + f": {got.get('reason')}")
    return int(a), snap


def terms(unit, hours=0):
    """Receipt terms from the unit as re-read at sign time. cert=True keeps the market's unit certificate (the kit
    attaches it); no durability, HACCP or special certificate is inferred from catalog labels. warranty_hours=0 is the
    explicit default off. Goods claim their current grade; food omits claim, because the kit's snapshot lags ~10s and
    a food grade ages, so a food grade claimed now may be false at delivery."""
    out = {"cert": True, "warranty_hours": hours if hours == warranty.HOURS and warranty.eligible(unit) else 0}
    if unit.get("kind") == "goods" and unit.get("grade") in ("A", "B", "C"):
        out["claim"] = unit["grade"]
    return out


def now():
    """The seller's clock in epoch seconds, the same scale as inventory expires_at."""
    return time.time()


def inventory():
    """(parsed inventory, sha256 of the exact bytes parsed). One read, so the hash names the data actually used.
    ({}, None) if the file cannot be read; ({}, sha) if it is not a JSON object."""
    try:
        raw = (MARKET_DIR / "inventory.json").read_bytes()
    except OSError:
        return {}, None
    try:
        inv = json.loads(raw.decode("utf-8"))
    except ValueError:
        inv = {}
    return (inv if isinstance(inv, dict) else {}), hashlib.sha256(raw).hexdigest()


def snapshot():
    """sha256 of the inventory file the quote was based on, or None if it cannot be read."""
    return inventory()[1]


def current(uid, t):
    """Re-read one reserved unit: (fresh unit, None, sha) if still sellable at t, else (None, "gone"|"expired", sha)."""
    inv, sha = inventory()
    fresh = next((x for x in inv.get("units", []) if x.get("unit") == uid), None) \
        if inv.get("round", 0) >= 3 else None
    reason = ("gone" if fresh is None else
              "expired" if fresh.get("grade") == "expired" or
              (fresh.get("expires_at") is not None and fresh["expires_at"] <= t) else None)
    return (None if reason else fresh), reason, sha


def journal(row):
    """Append one decision row to $MARKET_DIR/decisions.jsonl. Never raises: a broken journal must not stop trading.
    Rows carry ids, prices and who decided; never buyer text, model text or signatures."""
    try:
        line = json.dumps(dict(row, policy_version=POLICY_VERSION, ts=int(time.time())),
                          sort_keys=True, ensure_ascii=False, allow_nan=False)
        with open(MARKET_DIR / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except (OSError, TypeError, ValueError) as e:
        print(f"[journal] unavailable ({type(e).__name__}); trading continues", flush=True)


GRADES = ("A", "B", "C")
# "grade a" / "grade A" / "A grade" / "A등급" / "등급 A". "X grade" needs a capital letter: "a grade B" is grade B.
GRADE_RE = re.compile(r"(?i:\bgrade\s*[-:]?\s*)([abcABC])\b|\b([ABC])[\s-]*(?i:grade)\b|([ABC])\s*등급|등급\s*([ABC])\b")
NEGATION_RE = re.compile(r"(?i)\b(?:not|no|never|except|but|without|don'?t|can'?t|won'?t|isn'?t)\b|아닌|아니|말고|빼고|제외")
CONDITION_RE = re.compile(
    r"(?i)\b(?:if|unless|only|when|provided|or|and|both|each|every|per|units?|pieces?|pcs|qty|quantity|two|three|"
    r"several|bundle|together|warrant\w*|guarantee\w*|refund\w*|return\w*|coupon|review-coupon|discount\w*|"
    r"cheaper|lower|better|best|final|budget|max\w*|min\w*|least|most|days?|hours?|weeks?|shelf|life|expir\w*|"
    r"fresh\w*|negotiat\w*|bargain|deliver\w*|shipping|free)\b|[,;&%+/]|보증|환불|반품|할인|쿠폰|만약|경우|조건|이상|이하|"
    r"유통기한|신선|묶음|같이|그리고|또는|배송|무료")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
OFFER_RE = re.compile(r"(?i)^\s*(?:(?:i\s+)?(?:offer|can\s+(?:pay|do)|will\s+pay|pay)|how\s+about|my\s+offer\s+is|offer\s*:)?"
                      r"\s*(?:₩|krw\s*)?(\d{1,3}(?:,\d{3})+|\d+)\s*(?:원|krw|won)?\s*[.!?]?\s*$")
ACCEPT_RE = re.compile(r"(?i)^\s*(?:ok|okay|deal|accept(?:ed)?|i\s+accept|agreed|yes|좋아요|좋습니다|콜)\s*[.!]?\s*$")


CLAUSE_RE = re.compile(r"(?i)[.;,!?\n]|\b(?:but|and|however)\b|하지만|그리고|그런데")


def grade_wish(text):
    """Grades the buyer allows, or None if no grade was named. Read per clause: a negated clause excludes its grades,
    a plain clause names the wanted ones. "not grade C; grade A please" -> {A}; "not grade C" -> {A, B}.
    A contradiction ("grade A. not grade A") -> set(): no unit qualifies (fail closed, never a substitute)."""
    want, avoid = set(), set()
    for clause in CLAUSE_RE.split(text):
        named = {next(g for g in m.groups() if g).upper() for m in GRADE_RE.finditer(clause or "")}
        (avoid if NEGATION_RE.search(clause or "") else want).update(named)
    if not want and not avoid:
        return None
    return (want or set(GRADES)) - avoid


def strip_item(text, unit):
    """The buyer text without the unit's SKU and name, so their digits are never read as an offer."""
    out = text
    for k in (unit.get("sku"), unit.get("name")):
        if k:
            out = re.sub(re.escape(k), " ", out, flags=re.I)
    return out


def offer_in(text, unit):
    """The buyer's whole-number offer, or None: no number, more than one, a decimal, or a negated/conditional text
    (never the previous offer carried over, never a digit of the SKU or name)."""
    rest = strip_item(text, unit)
    nums = NUMBER_RE.findall(rest)
    if len(nums) != 1 or "." in nums[0] or NEGATION_RE.search(rest) or CONDITION_RE.search(rest.replace(nums[0], " ")):
        return None
    if not re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", nums[0]):
        return None
    return int(nums[0].replace(",", ""))


# Every word left after removing the item and a plainly named grade must be one of these, else the harness decides.
OPENING_WORDS = frozenset("""
i i'd id we want wanna would like to buy purchase get order take need please pls hi hello hey there
how much is it its it's the a an this that one what what's whats price cost costs does do you have has any in stock
available availability still sell selling for me can could tell about item product unit sku certified certificate
cert certification grade quality condition of quote credits credit
사고 살게요 사고싶어요 싶어요 싶습니다 구매 구매하고 구매할게요 주세요 원해요 얼마 얼마예요 얼마에요 얼마인가요
가격 가격은 가격이 재고 재고가 있나요 있어요 있습니까 등급 등급은 등급이 인증 인증서 인증서가 이 가 은 는 를 을 좀 요
""".split())


def fast_opening(text, unit):
    """True only for a plain first inquiry about this one unit: SKU/name/category plus allow-listed inquiry words
    (OPENING_WORDS: buy, price, availability, grade, certificate), at most one plainly named grade. Anything else --
    offers, conditions, negations, other items, unknown words -- goes to the harness."""
    rest = strip_item(text, unit)
    cat = (unit.get("category") or "").split(">")[-1]
    if cat:
        rest = re.sub(re.escape(cat), " ", rest, flags=re.I)
    wish = grade_wish(text)
    words = re.sub(r"(?i)\bgrade\s*[-:]?\s*[abc]\b|\b[ABC][\s-]*(?i:grade)\b|[ABC]\s*등급|등급\s*[ABC]\b", " ", rest)
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", words.lower())
    return (len(text) <= 300 and not NUMBER_RE.search(rest) and not NEGATION_RE.search(rest)
            and (wish is None or len(wish) == 1) and all(w in OPENING_WORDS for w in words))


def fast_followup(text, unit, ask):
    """("offer", n) for an exact whole-number offer, ("accept", ask) for a plain acceptance of the current ask,
    else None (the harness decides)."""
    rest = strip_item(text, unit).strip()
    m = OFFER_RE.match(rest)
    if m and not NEGATION_RE.search(rest):
        return "offer", int(m.group(1).replace(",", ""))
    if ask is not None and ACCEPT_RE.match(rest):
        return "accept", ask
    return None


def fast_move(unit, ask, offer, opening, min_accept):
    """The bounded fallback schedule (fallback_move) with one more guard: an offer at or above the current ask closes
    at the ask, so a signed price never exceeds the ask (and the ask never exceeds list). With a valid Sol min_accept,
    a strict-int offer at or above max(cost + 1, min_accept) closes now: the approved policy already allows it, so
    the 5%-of-list schedule need not walk down to it first. No Sol entry: the schedule alone, unchanged."""
    if ask is not None and offer is not None and offer >= ask:
        return "accept", ask
    if (ask is not None and min_accept is not None and type(offer) is int
            and offer >= max(unit["cost"] + 1, min_accept)):
        return "accept", min(offer, ask)
    return fallback_move(unit, ask, offer, opening, min_accept)


WARRANTY_POLICY = "warranty-0h-new-v1"   # every NEW quote is 0 h; no automatic 72 h without another reviewed policy
WARRANTY_RE = re.compile(r"(?i)\b(?:warrant\w*|guarantee\w*)\b|보증")
_W = r"(?:warrant\w*|guarantee\w*)"
# A plain waiver of any warranty ("no warranty needed", "without warranty", "warranty 0h", "보증 없이"): not a requirement.
WAIVER_RE = re.compile(
    rf"(?i)\b(?:no|zero|0+\s*(?:h|hrs?|hours?)?)\s+{_W}(?:\s+(?:is\s+)?(?:needed|required|necessary|fine|ok(?:ay)?))?"
    rf"|\bwithout\s+(?:an?\s+|any\s+)?{_W}"
    rf"|\b{_W}\s*(?:is\s+|of\s+)?[:=]?\s*0+\s*(?:h|hrs?|hours?)?(?![\w.])"
    rf"|\b{_W}\s+(?:is\s+)?(?:not|isn'?t)\s+(?:needed|required|necessary)"
    rf"|\b(?:don'?t|do\s+not)\s+need\s+(?:an?\s+|any\s+)?{_W}"
    r"|보증\s*(?:은|이|는)?\s*(?:없이|없어도|필요\s*없|0\s*시간|0(?!\d))")


def warranty_demand(text, unit=None):
    """None, or why the buyer text counts as a warranty requirement. Read per clause (item SKU/name removed): a clause
    naming a warranty is a requirement unless it is a plain waiver (WAIVER_RE) with no other negation left ("I won't
    buy without warranty" is a requirement). Fail closed: a bare question about warranty also counts."""
    rest = strip_item(text, unit) if unit else text
    for clause in CLAUSE_RE.split(rest):
        if not clause or not WARRANTY_RE.search(clause):
            continue
        left = WAIVER_RE.sub(" ", clause)
        if WARRANTY_RE.search(left):
            return "warranty_named"
        if left != clause and NEGATION_RE.search(left):
            return "negated_waiver"
    return None


def facts(unit, hours=0):
    """Honest public facts for a first reply: grade now, certificate, warranty, shelf life. Never cost or floor.
    New quotes are 0 h (warranty-0h-new-v1); hours=72 only for a deal that already held a 72 h intent."""
    if unit.get("kind") == "goods" and unit.get("grade") in GRADES:
        grade = f"grade {unit['grade']} now (claimed on the receipt)"
    else:
        grade = f"grade {unit.get('grade')} now (food grade ages, so the receipt does not claim it)"
    exp = unit.get("expires_at")
    life = ("no shelf-life expiry listed" if exp is None
            else f"about {max(0, int((exp - now()) // 3600))} h of shelf life left")
    return (f"Unit {unit['unit']}, {grade}, {life}; the market certificate of this unit stays on the receipt; "
            + ("72 h warranty from settlement (full-price refund), locked for this negotiation." if hours == warranty.HOURS
               else "no warranty (0 h)."))


# A SKU-shaped token in buyer text ("G" + hex with at least one digit, any case: G00001, Ga2e3075f, g0000BEEF). One that
# names no unit in stock makes the request unknown: no name, category or partial match may stand in for it.
SKU_TOKEN_RE = re.compile(r"(?i)(?<![0-9a-z])g(?=[0-9a-f]*\d)[0-9a-f]{5,}(?![0-9a-z])")
PARTIAL_MIN_CHARS = 6   # letters/digits of a selecting fragment, spaces excluded ("순면 베개커버" = 6). Assumed, not tuned.
PARTIAL_MAX_CHARS = 2048   # longer buyer text: no partial match (bounds the scan on untrusted text). Assumed, not tuned.
PARTIAL_MAX_WORDS = 64


def partial_sku(text, units):
    """The one SKU whose name the text names in part, else None. A fragment is a run of >= 2 whole words of the text
    that appears as consecutive whole words of the name and holds at least one real word (letters only, >= 2 long,
    not an inquiry word): digits/sizes alone ("50x70 17") or "for the" never pick a product. A SKU whose every fragment
    lies strictly inside a longer fragment of another SKU is out ("젤라또 베개커버 50x70" -> the 50x70 SKU). Any other
    SKU left with a fragment -- same words, overlapping or disjoint fragments of two products -- means no SKU (fail
    closed, never the longest). The winner needs a fragment of >= PARTIAL_MIN_CHARS letters/digits.
    Text over PARTIAL_MAX_CHARS characters or PARTIAL_MAX_WORDS words matches nothing; each (sku, name) is scanned once."""
    if len(text) > PARTIAL_MAX_CHARS:
        return None
    said = [w.lower() for w in re.findall(r"\w+", text)]
    if len(said) > PARTIAL_MAX_WORDS:
        return None
    word = lambda w: w.isalpha() and len(w) >= 2 and w not in OPENING_WORDS
    spans = {}   # sku -> {(i, j)}: said[i:j] is a fragment of that SKU's name
    for sku, raw in dict.fromkeys((u["sku"], u.get("name") or "") for u in units):
        name = [w.lower() for w in re.findall(r"\w+", raw)]
        for i in range(len(said)):
            for j in range(i + 2, len(said) + 1):
                run = said[i:j]
                if not any(name[k:k + len(run)] == run for k in range(len(name) - len(run) + 1)):
                    break   # a longer run from i cannot fit either
                if any(word(w) for w in run):
                    spans.setdefault(sku, set()).add((i, j))
    inside = lambda sp, o: any(a <= sp[0] and sp[1] <= b and (a, b) != sp for a, b in spans[o])
    left = [s for s in spans if not all(any(inside(sp, o) for o in spans if o != s) for sp in spans[s])]
    if len(left) != 1:
        return None
    big = any(sum(len(w) for w in said[i:j]) >= PARTIAL_MIN_CHARS for i, j in spans[left[0]])
    return left[0] if big else None


def find_unit(text, taken=(), inv=None):
    """The in-stock unit that fits what the buyer asked for and expires first (FEFO, then oldest supply), skipping
    units already promised in another open negotiation or held after a sale, and any unit whose expires_at <= now()
    whatever its grade. A named SKU beats a name match, which beats a category match, which beats a unique partial
    name (partial_sku). An unknown SKU-shaped token or two different named SKUs match nothing. A named grade is
    strict: no other grade is ever substituted (grade_wish)."""
    want = text.lower()
    if inv is None:
        inv, _ = inventory()
    if inv.get("round", 0) < 3:
        # Setup migration is Round 3 only: missing/stale state must not invent stock.
        return None
    units = inv.get("units", [])
    t = now()
    wish = grade_wish(text)
    known = {str(u.get("sku")).lower() for u in units}
    if any(s.lower() not in known for s in SKU_TOKEN_RE.findall(text)):
        return None
    has = lambda k, u: bool(k(u)) and k(u).lower() in want   # an empty name/category matches nothing
    sku, name = (lambda u: u["sku"]), (lambda u: u.get("name"))
    cat = lambda u: (u.get("category") or "").split(">")[-1]
    by_sku = [u for u in units if has(sku, u)]
    if len({u["sku"] for u in by_sku}) > 1:
        return None   # two SKUs named: ambiguous, never the earliest-expiring of both
    # What was asked for is decided on ALL units first (SKU > name > category > partial name); eligibility filters
    # only that set, so a named SKU with no eligible unit is no_stock, never another SKU of the same category or name.
    asked = by_sku or [u for u in units if has(name, u)] or [u for u in units if has(cat, u)]
    if not asked:
        part = partial_sku(text, units)
        asked = [u for u in units if part is not None and u["sku"] == part]
    fit = [u for u in asked if u["grade"] != "expired" and u["unit"] not in taken and
           (u["expires_at"] is None or u["expires_at"] > t) and (wish is None or u["grade"] in wish)]
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


def open_discount(text, task_id, unit, normal_quote, t):
    """Review-discount pilot, task open only: (reservation or None, journal note or None). No coupon -> (None, None)
    and no pilot file is touched, so the normal path is unchanged. Local files only (no network)."""
    post = review_discount.parse_coupon(text)
    if post is None:
        return None, None
    try:
        res, reason = review_discount.reserve(MARKET_DIR, post, task_id, unit, normal_quote, t)
    except Exception as e:   # the discount is optional: any failure means the regular price
        res, reason = None, f"error:{type(e).__name__}"
    if res is None:
        return None, {"post": post, "outcome": reason}
    return res, discount_note(res)


def discount_note(res, **extra):
    """Journal fields of one reservation: evidence ids, policy, normal/discounted quote, cost, budget. No buyer text."""
    note = {k: res.get(k) for k in ("id", "post", "settle_seq", "author", "normal_quote", "discount", "discounted_quote",
                                     "cost", "policy", "accounting", "status")}
    note["budget_reserved"] = res.get("discount")   # opening-incentive upper bound, not the causal cost of the deal
    return dict(note, **extra)


def pilot_floor(unit, disc, min_accept=None):
    """Lowest acceptable price: floor(unit), or on a review-discount task the integer cost+1 (review_discount.min_price).
    A Sol min_accept raises the floor of a non-discount task (never lowers it below cost)."""
    if disc is not None:
        return review_discount.min_price(floor(unit))
    return floor(unit) if min_accept is None else max(floor(unit), min_accept)


def release_warranty(deal, why):
    """Free this task's unsigned warranty intent (an issued authority is kept). Never raises."""
    if deal.get("warranty") == warranty.HOURS:
        try:
            got = warranty.release(MARKET_DIR, deal["src"]["task"], why, now())
        except Exception as e:
            got = f"error:{type(e).__name__}"
        journal(dict(deal["src"], event="warranty_release", reason=why, outcome=got))


def release_discount(deal, why):
    """Free an unsigned reservation (review_discount keeps a signed one: it may still be paid late)."""
    if deal.get("disc"):
        review_discount.release(MARKET_DIR, deal["src"]["task"], why, now())


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}   # task id -> {"unit", "ask", "offer", "first", "src", "anchor", "policy", "lines", "touched"}
        self.held = {}   # unit id -> {"task", "at"}: sold, held COMPLETED_HOLD seconds against a lagging snapshot
        self.stale = {}  # task id -> unit id whose reservation went stale; the task is never resumed
        try:   # durable tracker restore: validate the signed state, free intents of earlier boots (never signed)
            print(f"[warranty] startup {warranty.startup(MARKET_DIR, now())}", flush=True)
        except Exception as e:   # corrupt/unreadable: kept as is (never reset); every new quote says 0 h
            print(f"[warranty] startup failed closed ({type(e).__name__}: {e}); quotes are 0 h", flush=True)

    def sweep(self, t):
        """Release reservations idle longer than RESERVATION_TTL and holds older than COMPLETED_HOLD."""
        for tid, d in list(self.open.items()):
            if t - d["touched"] > RESERVATION_TTL:
                del self.open[tid]
                self.stale[tid] = d["unit"]["unit"]
                journal(dict(d["src"], event="reservation_stale"))
                release_discount(d, "stale")
                release_warranty(d, "stale")
        for uid, h in list(self.held.items()):
            if t - h["at"] > COMPLETED_HOLD:
                del self.held[uid]
                journal({"event": "hold_expired", "task": h["task"], "unit": uid})

    async def execute(self, context, event_queue):
        t0 = time.perf_counter()
        t = now()
        self.sweep(t)
        task = context.current_task
        continuing = task is not None
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        fast = False   # True: deterministic_fastpath decides this turn, no harness call

        if deal is None and continuing:
            # The reservation was released: reject, never reopen the old task on a new unit.
            journal({"event": "unit_unavailable", "task": task.id, "unit": self.stale.get(task.id),
                     "reason": "stale", "snapshot": snapshot()})
            await up.reject(say("This negotiation has expired. Please start a new one."))
            return
        if deal is None:
            inv, sha = inventory()
            taken = {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]} | set(self.held)
            unit = find_unit(text, taken, inv)
            if unit is None:
                journal({"event": "no_stock", "task": task.id, "snapshot": sha})
                wish = grade_wish(text)
                await up.reject(say("Sorry, we have none of that in stock" +
                                    (f" in grade {'/'.join(sorted(wish))}." if wish else ".")))
                return
            why = warranty_demand(text, unit)
            if why:
                await self.decline_warranty(up, say, {"task": task.id, "unit": unit["unit"], "sku": unit["sku"]},
                                            why, "first")
                return
            # The price policy is read once, here: later anchors apply to new tasks only, never to this one.
            sol_policy.maybe_refresh_async(MARKET_DIR)   # lazy cadence: background only, this turn never waits
            anchor, policy = price_policy(unit, t)
            src = {"task": task.id, "unit": unit["unit"], "sku": unit["sku"], "cost": unit["cost"],
                   "list_price": unit["list_price"], "snapshot": sha, "price_policy": policy}
            disc, note = open_discount(text, task.id, unit, first_quote(unit, anchor), t)
            if note is not None:
                src["review_discount"] = note
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "first": None, "src": src,
                                         "anchor": anchor, "policy": policy, "disc": disc,
                                         "lines": [f"buyer: {text}"], "touched": t}
            fast = FASTPATH and disc is None and note is None and fast_opening(text, unit)
        else:
            # A task opened before policy snapshots keeps the bounded-v1 schedule; never re-read an anchor mid-task.
            deal.setdefault("anchor", None)
            deal.setdefault("policy", dict(LEGACY_POLICY))
            if deal.get("disc") and not review_discount.active(review_discount.load_config(MARKET_DIR), t):
                # The pilot window closed on an unsigned discounted task: never sign it, never silently re-price it.
                del self.open[task.id]
                release_discount(deal, "expired")
                release_warranty(deal, "expired")
                journal(dict(deal["src"], event="review_discount_expired"))
                await up.reject(say("The review discount has expired. Please start a new negotiation."))
                return
            # Every turn re-reads inventory: the unit may have been sold elsewhere or expired since the quote.
            fresh, reason, sha = current(deal["unit"]["unit"], t)
            if reason:
                del self.open[task.id]
                journal(dict(deal["src"], event="unit_unavailable", reason=reason, snapshot=sha))
                release_discount(deal, "unit_unavailable")
                release_warranty(deal, "unit_unavailable")
                await up.reject(say("Sorry, that item is no longer available."))
                return
            deal["unit"] = fresh
            deal["touched"] = t
            why = warranty_demand(text, fresh) if deal.get("warranty") != warranty.HOURS else None
            if why:   # a 0 h deal never takes on a warranty mid-negotiation; an already-promised 72 h deal is unchanged
                del self.open[task.id]
                release_discount(deal, "warranty_demand")
                await self.decline_warranty(up, say, deal["src"], why, "later")
                return
            deal["lines"].append(f"buyer: {text}")
            got = fast_followup(text, fresh, deal["ask"]) if FASTPATH and not deal.get("disc") else None
            if got is not None:
                fast = True
                deal["offer"] = got[1]
            else:
                deal["offer"] = offer_in(text, fresh)   # this turn's valid offer or None: never a stale carry-over

        u = deal["unit"]
        disc = deal.setdefault("disc", None)
        # Review-discount task: opens at the reserved discounted quote and never goes to/below cost (cost + 1).
        opening = deal["anchor"] if disc is None else disc["discounted_quote"]
        target, min_accept = sol_terms(deal["policy"]) if disc is None else (None, None)
        reserve = pilot_floor(u, disc, min_accept)
        # Opening: capped by this task's anchor. Later turns: capped by the last ask, so a quote never rises.
        cap = first_quote(u, opening) if deal["ask"] is None else deal["ask"]
        if fast:
            # No harness call on this turn: the tested bounded schedule decides; telemetry says so (no stale LAST_CALL).
            action, price = fast_move(u, deal["ask"], deal["offer"], opening, min_accept)
            if action == "accept":
                deal["offer"] = price
            move, call, decided_by = {"action": action}, {}, "deterministic_fastpath"
            print(f"[fastpath] {action} offer={deal['offer']} quote={price}", flush=True)
        else:
            openai_harness.LAST_CALL.clear()   # a harness that records nothing must not inherit an older call
            move, raw = ask_json(PROMPT.format(name=u["name"], list_price=u["list_price"], reserve=reserve,
                                               anchor="none" if deal["anchor"] is None else deal["anchor"],
                                               policy=deal["policy"]["label"], cap=cap,
                                               target="" if target is None else f" Target settlement {target}.",
                                               transcript="\n".join(deal["lines"])))
            call = dict(openai_harness.LAST_CALL)
            print(f"[harness] {raw}", flush=True)
            move = move if isinstance(move, dict) else {}
            action, decided_by = move.get("action"), "model"
            price = model_price(u, cap, move.get("price")) if action == "counter" else None
        if not fast and action not in ("accept", "reject") and price is None:
            # No valid model decision (harness unavailable, malformed, or a non-int price): deterministic bounded-v1 step.
            action, price = fallback_move(u, deal["ask"], deal["offer"], opening, min_accept)
            decided_by = "fallback"
            print(f"[fallback] deterministic {action} (no model decision) offer={deal['offer']} quote={price}", flush=True)
        elif not fast and action == "counter" and price != move.get("price"):
            print(f"[guard] ask {move.get('price')} outside [{reserve}, {cap}] -> {price}", flush=True)
        if action == "accept":
            # The model call takes seconds: re-read before signing, so a unit sold or expired meanwhile is never
            # signed and the cost guard and claim use the unit as it is now, not as it was at the quote.
            fresh, reason, sha = current(u["unit"], now())
            if reason:
                self.open.pop(task.id, None)
                journal(dict(deal["src"], event="unit_unavailable", reason=reason, snapshot=sha))
                release_discount(deal, "unit_unavailable")
                release_warranty(deal, "unit_unavailable")
                await up.reject(say("Sorry, that item is no longer available."))
                return
            u = deal["unit"] = fresh
            reserve = pilot_floor(u, disc, min_accept)
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below cost {reserve} -> counter", flush=True)
            action, price = fallback_move(u, deal["ask"], deal["offer"], opening, min_accept)
            decided_by = "fallback"
            if disc is not None and action == "accept":
                # fallback_move accepts at cost; the discounted floor is cost+1: counter there instead.
                action, price = "counter", reserve
        if action == "counter" and (disc is not None or min_accept is not None):
            # Sol min_accept <= opening and every later ask >= reserve, so this never lifts a quote above the last ask.
            price = max(price, reserve)
        step = dict(deal["src"], offer=deal["offer"], decided_by=decided_by, warranty_hours=deal.get("warranty", 0),
                    model_call={k: call.get(k) for k in ("requested", "returned", "input", "output", "latency_s")},
                    elapsed_ms=round((time.perf_counter() - t0) * 1000, 3))
        if action == "accept":
            # Sign the int: the kit turns the wire double (120.0) back into 120 before it verifies (market_kit.py:736).
            price = int(deal["offer"])
            if disc is not None:
                # Persist reserved -> signed BEFORE signing; if that fails, no discounted receipt exists.
                got = review_discount.mark_signed(MARKET_DIR, task.id, price, u["unit"], u["sku"], now(), cost=u["cost"])
                if got != "signed":
                    self.open.pop(task.id, None)
                    release_discount(deal, f"mark_signed:{got}")
                    release_warranty(deal, f"mark_signed:{got}")
                    journal(dict(step, event="review_discount_unsigned", review_discount=discount_note(disc, outcome=got)))
                    await up.reject(say("Sorry, this discounted deal could not be recorded. Please start a new negotiation."))
                    return
                step["review_discount"] = discount_note(disc, status="signed", signed_price=price)
            if deal.get("warranty") == warranty.HOURS:
                # Persist the authority BEFORE signing; any failure refuses unsigned (never a silent 0 h receipt).
                got = warranty.issue(MARKET_DIR, task.id, u, price, now(), shop=NAME)
                if got != "issued":
                    self.open.pop(task.id, None)
                    release_discount(deal, f"warranty:{got}")
                    release_warranty(deal, f"issue:{got}")
                    journal(dict(step, event="warranty_unsigned", warranty_outcome=got))
                    await up.reject(say("The 72 h warranty quoted for this deal could not be confirmed, so no receipt "
                                        "was signed. Please start a new negotiation for a fresh quote."))
                    return
                step["warranty"] = {"policy": warranty.POLICY, "hours": warranty.HOURS, "outcome": got}
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
            receipt.update(terms(u, deal.get("warranty", 0)))   # outside the signed body: sign() covers shop, item, price, task only
            receipt = sign(receipt)
            # Negotiation complete is not payment: the kit settles; this row never claims paid/settled.
            journal(dict(step, event="negotiation_complete", first_quote=deal["first"], final_quote=deal["ask"],
                         price=price))
            # Hold before any await: a concurrent cancel/sweep must not free the signed unit or KeyError here.
            self.open.pop(task.id, None)
            if u["unit"]:
                self.held[u["unit"]] = {"task": task.id, "at": t}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            return
        if action == "reject":
            journal(dict(step, event="reject", first_quote=deal["first"], final_quote=deal["ask"]))
            self.open.pop(task.id, None)
            release_discount(deal, "reject")
            release_warranty(deal, "reject")
            await up.reject(say("No deal."))
            return
        if deal["ask"] is None:
            # warranty-0h-new-v1: no warranty.quote intent, no reserve, for every new quote (even with pilot room).
            deal["warranty"] = 0
            step["warranty_hours"] = 0
            step["warranty_policy"] = {"policy": WARRANTY_POLICY, "hours": 0, "reserve": 0,
                                       "reason": "new_quote_default_0h", "eligible_grade": warranty.eligible(u)}
        journal(dict(step, event="first_quote" if deal["ask"] is None else "counter", quote=price))
        if deal["first"] is None:
            deal["first"] = price
        deal["ask"] = price
        # Facts first: the ask stays the LAST number of the reply, as buyers parsing the old text expect.
        reply = (f"{facts(u, deal.get('warranty', 0))} List {u['list_price']}. {u['name']}: asking {deal['ask']}. Send your offer as a number."
                 if len(deal["lines"]) == 1
                 else f"Counteroffer: {deal['ask']}.")
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    @staticmethod
    async def decline_warranty(up, say, src, why, turn):
        """Reject unsigned: no new warranty authority is created and no 0 h receipt is signed against a requirement."""
        journal(dict(src, event="warranty_demand_rejected",
                     warranty_policy={"policy": WARRANTY_POLICY, "hours": 0, "reserve": 0, "reason": why, "turn": turn}))
        await up.reject(say("Sorry, this shop offers no new warranty: new quotes are 0 h (no warranty), so a deal that "
                            "requires a warranty cannot be signed and no receipt was signed. If a 0 h deal is fine, "
                            "please start a new negotiation without the warranty requirement."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        d = self.open.pop(task.id, None)
        if d is not None:
            journal(dict(d["src"], event="cancel"))   # released at once, not after the TTL
            release_discount(d, "cancel")
            release_warranty(d, "cancel")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    pilot = review_discount.card_text(MARKET_DIR, now())   # '' unless the review-discount pilot is active now
    card = AgentCard(
        name=NAME,
        description=f"A small online shop. Ask for an item by name or SKU, or by a part of its name (two or more whole "
                    f"words) that fits only one product; an unknown SKU or an ambiguous part gets no quote. Plain inquiries and whole-number offers are answered "
                    f"by fixed rules (no model call); other messages go to a coding harness ({CMD.split()[0]}). "
                    f"Goods grade claimed on the receipt, market certificate kept. Warranty: new quotes carry no warranty "
                    f"(0 h); a request that requires a warranty is declined unsigned (no new warranty is offered)."
                    + (f" {pilot}" if pilot else ""),
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item, then send a price. The shop answers with a counteroffer or a receipt."
                                       + (" Optional: add REVIEW-COUPON <forum post id> to the first message." if pilot else ""),
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 알뜰맘 포기김치 10kg (G00001)", "52000"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def review_discount_loop(every=60):
    """Seller daemon thread: evidence refresh + settlement reconciliation, never inside a seller turn. Never raises."""
    while True:
        try:
            review_discount.tick(MARKET_DIR, time.time())
        except Exception as e:
            print(f"[review-discount] tick failed ({type(e).__name__}); regular trading continues", flush=True)
        time.sleep(every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    if (MARKET_DIR / review_discount.CONFIG).exists():   # only once the parent has activated the pilot
        threading.Thread(target=review_discount_loop, daemon=True).start()
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
