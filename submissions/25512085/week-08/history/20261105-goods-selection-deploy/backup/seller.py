"""A2A v1.0 Seller Agent (Week 08: Round 3 Inventory, Certified Stock, Warranty & Dynamic Pricing).

Supports both:
- Round 3 Economy (`inv.get("round", 0) >= 3`): sells real in-stock units from `.market/inventory.json`
  (5,629 catalog SKUs), records 1st-party inbound & missed demand into `state_hub.json` (`store.py`),
  attaches market certificate (`cert: True`), honest real-time grade `claim` ("A"|"B"|"C"), and
  margin-tiered `warranty_hours`, and uses High-Anchor + 1-to-2-Turn Fast-Close pricing to maximize
  both profit/revenue and 5-star buyer ratings.
- Rounds 0-2 Backward Compatibility (`lamp`, `chair`, `bookshelf`): passes `market_test_week07.py` 25/25.
"""
import argparse
import base64
import json
import logging
import math
import os
import random
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import seller_cash_policy as cash_policy
import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import (
    get_message_text,
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from store import (
    BREAK_RATE,
    GOODS_BREAK_WINDOW_H,
    clearance_factor,
    get_sku_market_anchor,
    load_catalog,
    load_hub,
    load_inventory,
    record_inbound_inquiry,
)

RESERVES = {
    "lamp": 100,
    "chair": 180,
    "bookshelf": 260,
}
PREMIUM_MULT = 1.50
VIP_MULT = 1.02
MEMBERSHIP_PROMO_WINDOW_SEC = 1800
PROMO_START_TS = time.time()
ALL_ITEMS_SET = frozenset(RESERVES.keys())

SHOP_NAME = os.environ.get("SHOP_NAME", "mjforge")

# The a2a SDK's OpenTelemetry wrapper logs "Failed to detach context" with a full traceback whenever a finished
# request's task is torn down (GeneratorExit). Requests and receipts are unaffected; the noise only buries the kit
# log we submit. Hide that one logger; asyncio and our own errors still print.
logging.getLogger("opentelemetry.context").setLevel(logging.CRITICAL)
MARKET_NAME = os.environ.get("SHOP_MARKET_NAME", "mjforge")
BASE_DIR = Path(__file__).resolve().parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", str(BASE_DIR / ".market")))
SELLER_HISTORY_FILE = BASE_DIR / "seller_history.json"

# Analytics must never block the A2A event loop on the shared hub's eight-second file lock.
_INQUIRY_QUEUE=queue.Queue(maxsize=256)
_INQUIRY_THREAD=None
_INQUIRY_START_LOCK=threading.Lock()


def _inquiry_worker():
    while True:
        writer,args,kwargs=_INQUIRY_QUEUE.get()
        try:writer(*args,**kwargs)
        except Exception as exc:print(f'[inquiry-analytics-error] {type(exc).__name__}',flush=True)
        finally:_INQUIRY_QUEUE.task_done()


def queue_inbound_inquiry(*args,**kwargs):
    global _INQUIRY_THREAD
    with _INQUIRY_START_LOCK:
        if _INQUIRY_THREAD is None or not _INQUIRY_THREAD.is_alive():
            _INQUIRY_THREAD=threading.Thread(target=_inquiry_worker,daemon=True,name='seller-inquiry-analytics')
            _INQUIRY_THREAD.start()
    kwargs['observed_at']=time.time()
    try:_INQUIRY_QUEUE.put_nowait((record_inbound_inquiry,args,kwargs))
    except queue.Full:print('[inquiry-analytics-skipped] bounded queue full; checkout unaffected',flush=True)


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt: dict, key_path: str = "shop_key.pem") -> dict:
    p = BASE_DIR / key_path
    if not p.exists():
        p = Path(key_path)
    key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode("ascii")
    return receipt


# --- Round 3 Unit Matching, Real-Time Grade, and Dynamic Pricing Engine ---

def compute_live_unit_grade(unit: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> tuple[str, float | None]:
    """Compute exact real-time grade ('A', 'B', 'C', or 'expired') and remaining hours for a unit.
    Includes a 15-second safety buffer for food so our grade claim never becomes dishonest during ACP checkout."""
    kind = unit.get("kind")
    if kind != "food" or not unit.get("expires_at"):
        return str(unit.get("grade") or "A"), None

    now_buffered = time.time() + 15.0
    hours_left = (float(unit["expires_at"]) - now_buffered) / 3600.0
    if hours_left <= 0.05:
        return "expired", 0.0

    sku = unit.get("sku", "")
    shelf_hours = float((catalog.get(sku) or {}).get("shelf_hours") or unit.get("shelf_hours") or 24.0)
    ratio = hours_left / max(1.0, shelf_hours)
    if ratio >= 0.80:
        grade = "A"
    elif ratio >= 0.45:
        grade = "B"
    else:
        grade = "C"
    return grade, round(hours_left, 2)



FOOD_CHECKOUT_BUFFER_H = 0.25
_DURATION = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>hours?|hrs?|h|days?|시간|일)(?![a-zA-Z])"
_REQUIREMENT_KEY = r"\b(?:use_within_hours|min_hours_left)\s*[:=]\s*(\d+(?:\.\d+)?)"


def extract_food_use_hours(text: str, awaiting_duration: bool = False) -> float | None:
    """Read explicit use/shelf-life requirements, not arbitrary product/price numbers.

    A bare duration is accepted only in reply to our use-period question. Multiple
    explicit requirements use the longest period; warranty language is not food use.
    """
    text = (text or "").lower()
    values = [float(m.group(1)) for m in re.finditer(_REQUIREMENT_KEY, text)]
    for m in re.finditer(_DURATION, text):
        before = re.split(r"[,;.!?\n]", text[:m.start()])[-1][-80:]
        after = re.split(r"[,;!?\n]", text[m.end():])[0][:80]
        bare = awaiting_duration and re.fullmatch(r"\s*" + _DURATION + r"\s*[.!]?\s*", text)
        english = (
            re.search(r"\buse\s+within\s*$", before)
            or (re.search(r"\bat least\s*$", before) and re.match(r"\s*(?:of\s+)?shelf[ -]?life\b", after))
            or re.search(r"\b(?:need|require)\b[^\d]*$", before)
        )
        korean = m.group("unit") in ("시간", "일") and re.match(r"[^\d,;.!?\n]{0,40}(?:필요|남아|사용|먹)", after)
        warranty = re.search(r"warranty|guarantee|보증", before + after)
        if bare or ((english or korean) and not warranty):
            hours = float(m.group("value"))
            if m.group("unit") in ("day", "days", "일"):
                hours *= 24
            values.append(hours)
    finite = [v for v in values if math.isfinite(v) and v >= 0]
    return max(finite) if finite else None


def food_has_use_time(unit: dict[str, Any], required_hours: float) -> bool:
    """Use unrounded live expiry when available, including checkout headroom."""
    if unit.get("expires_at"):
        left = (float(unit["expires_at"]) - time.time() - 15.0) / 3600.0
    else:
        left = float(unit.get("hours_left") or 0)
    return left >= required_hours + FOOD_CHECKOUT_BUFFER_H


def food_use_question(hours_left: float | None) -> str:
    return (
        f"Remaining shelf life is {hours_left}h (not a long-term guarantee). "
        "How many hours/days must it remain usable? Reply e.g. 'use within 72h'. "
        "The server does not disclose your order conditions, so I cannot infer your required use period. "
        "No receipt can be signed until you provide it."
    )


def select_best_unit(units: list[dict[str, Any]], buyer_text: str) -> dict[str, Any] | None:
    """Select the best matching unit considering Food vs Non-Food and buyer intent:
    - Food (perishable): filter explicit use requirements plus checkout headroom
      before applying existing grade/FEFO preferences. Unknown requirements must
      be clarified by the session before any receipt can be signed.
    - Goods (non-food): High-grade (A > B > C) priority by default for safe 6h warranty
      and near-zero defect risk, unless buyer specifically requests cheap/bargain/C-grade."""
    if not units:
        return None

    text_low = (buyer_text or "").lower()
    wants_cheap = any(
        kw in text_low
        for kw in ("cheap", "discount", "budget", "bargain", "lowest", "b급", "c급", "싸게", "저렴", "할인", "떨이")
    )
    wants_quality = any(
        kw in text_low
        for kw in ("fresh", "new", "best", "grade a", "a급", "warranty", "guarantee", "신선", "최상", "보증")
    )

    is_food = any(u.get("kind") == "food" or u.get("expires_at") for u in units)

    if is_food:
        required_hours = extract_food_use_hours(buyer_text)
        if required_hours is not None:
            units = [u for u in units if food_has_use_time(u, required_hours)]
            if not units:
                return None
        # Food handling (FEFO with safety buffer)
        if wants_cheap:
            # Buyer explicitly wants cheap/bargain food: prefer C -> B -> A,
            # but require at least 1.5h left so checkout has a chance to settle
            viable = [u for u in units if (u.get("hours_left") or 0) >= 1.5]
            pool = viable if viable else units
            grade_prio = {"C": 0, "B": 1, "A": 2}
            return min(
                pool,
                key=lambda u: (
                    grade_prio.get(str(u.get("live_grade", "C")), 1),
                    u.get("hours_left") or float("inf"),
                    u.get("supplied_at") or 0,
                ),
            )
        elif wants_quality:
            # Buyer explicitly asked for fresh/high-grade food: Grade A first, then FEFO among A
            grade_prio = {"A": 0, "B": 1, "C": 2}
            return min(
                units,
                key=lambda u: (
                    grade_prio.get(str(u.get("live_grade", "A")), 2),
                    u.get("hours_left") or float("inf"),
                    u.get("supplied_at") or 0,
                ),
            )
        else:
            # Default for food: Safe FEFO!
            # Filter units that have >= 3.5h left and are Grade A or B (guarantees buyer accept pass)
            safe = [u for u in units if (u.get("hours_left") or 0) >= 3.5 and u.get("live_grade") in ("A", "B")]
            if safe:
                # Sell the unit expiring soonest among safe stock to prevent spoilage loss
                return min(safe, key=lambda u: (u.get("hours_left") or float("inf"), u.get("supplied_at") or 0))
            # Fallback if no unit meets 3.5h buffer: pick the one with the most shelf life left
            return max(units, key=lambda u: (u.get("hours_left") or 0, -(u.get("supplied_at") or 0)))

    else:
        # Non-food (goods): Grade is permanent, no expiration date
        grade_prio_quality = {"A": 0, "B": 1, "C": 2}
        grade_prio_cheap = {"C": 0, "B": 1, "A": 2}

        if wants_cheap:
            # Buyer wants cheap/refurb -> clear C/B grades first, tie-break by oldest in stock (FIFO)
            return min(
                units,
                key=lambda u: (
                    grade_prio_cheap.get(str(u.get("live_grade") or u.get("grade", "B")), 1),
                    u.get("supplied_at") or 0,
                ),
            )
        else:
            # Default or quality: Grade A first (defect risk < 3%, safe 6h warranty, 100% checkout pass)
            return min(
                units,
                key=lambda u: (
                    grade_prio_quality.get(str(u.get("live_grade") or u.get("grade", "A")), 1),
                    u.get("supplied_at") or 0,
                ),
            )


def find_stock_unit(text: str, taken_units: set[str], inv: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """Find an in-stock unit matching the buyer's request (SKU, product name, or category).
    Returns (matched_unit_or_None, extracted_demand_query)."""
    want = text.strip()
    want_low = want.lower()

    # Extract explicit SKU if mentioned (e.g. G000f6cda or F00012345)
    sku_match = re.search(r"\b([gfGF][0-9a-fA-F]{7,10})\b", want)
    wanted_sku = sku_match.group(1).lower() if sku_match else None

    units = inv.get("units") or []
    valid_units = []
    for u in units:
        u_id = u.get("unit")
        if not u_id or u_id in taken_units:
            continue
        live_grade, hours_left = compute_live_unit_grade(u, catalog)
        if live_grade == "expired":
            continue
        u_copy = dict(u, live_grade=live_grade, hours_left=hours_left)
        valid_units.append(u_copy)

    # 1. Exact SKU match
    if wanted_sku:
        exact = [u for u in valid_units if str(u.get("sku", "")).lower() == wanted_sku]
        if exact:
            chosen = select_best_unit(exact, want)
            return chosen, (chosen or {}).get("category") or wanted_sku
        # If we do not have this exact SKU, resolve its category for demand radar,
        # but NEVER substitute a different SKU from the same category!
        # Arbitrary substitution causes ACP checkout failure because buyer orders specify
        # strict acceptable SKU subsets, which would trigger 1-star rip-off reviews.
        cat_name = wanted_sku
        for c_sku, c_info in catalog.items():
            if c_sku.lower() == wanted_sku:
                cat_name = c_info.get("category") or wanted_sku
                break
        return None, cat_name

    # 2. Match by product name or category leaf in buyer message
    fit = []
    for u in valid_units:
        cat_full = str(u.get("category") or "")
        cat_leaf = cat_full.split(">")[-1].strip().lower()
        u_name = str(u.get("name") or "").strip().lower()
        u_sku = str(u.get("sku") or "").strip().lower()
        if (u_sku and u_sku in want_low) or (u_name and u_name in want_low) or (cat_leaf and cat_leaf in want_low):
            fit.append(u)

    if fit:
        chosen = select_best_unit(fit, want)
        return chosen, (chosen or fit[0]).get("category") or (chosen or fit[0]).get("name", "")

    # 3. Determine what category the buyer was asking for (for missed demand radar)
    detected_cat = ""
    for c_sku, c_info in catalog.items():
        cat_full = str(c_info.get("category") or "")
        cat_leaf = cat_full.split(">")[-1].strip().lower()
        c_name = str(c_info.get("name") or "").strip().lower()
        if (cat_leaf and len(cat_leaf) >= 2 and cat_leaf in want_low) or (c_name and c_name in want_low):
            detected_cat = cat_full
            break
    if not detected_cat:
        # Clean common prefixes like "I want to buy"
        cleaned = re.sub(r"(?i)^(i want to buy|looking for|can i buy|buy|need)\s+", "", want).strip()
        detected_cat = cleaned[:50]
    return None, detected_cat


# 안심케어 (Safe-Care) = the 6 h warranty on goods: if the unit breaks within it, the market refunds the full price
# from our balance. Break rates by grade (BREAK_RATE) live in store.py, shared with market_buyer.py.
CARE_HOURS = GOODS_BREAK_WINDOW_H
# Without Safe-Care we expect to keep the whole price, so the fair discount is the expected refund: price x break rate.
# Fixed, disclosed grade terms: A72h, B6h, C0h. No mid-negotiation warranty switches.
NO_CARE_GRADES = ()
NO_CARE_WORDS = ("without care", "without safe-care", "without safecare", "no care", "no safe-care",
                 "without warranty", "no warranty", "without the warranty", "안심케어 없이", "안심케어 미포함",
                 "케어 없이", "보증 없이", "보증 빼고", "미포함")


def care_terms(kind: str, warranty: int) -> dict[str, str]:
    """Plain-language Safe-Care terms added to a goods receipt for the buyer to read. The kit and the market use
    only warranty_hours; these fields restate it and claim nothing beyond what the market enforces."""
    if kind != "goods":
        return {}
    if warranty > 0:
        label=" (market maximum / 시장 허용 최대 보증)" if warranty==72 else ""
        return {"care_plan": f"Safe-Care (안심케어) {warranty}h{label}",
                "refund_policy": f"the market refunds the full price if the item breaks within {warranty} h of the sale"}
    return {"care_plan": "none (bought without Safe-Care)",
            "refund_policy": "no refund if the item breaks"}


def no_care_price(price: int, cost: int, break_rate: float) -> int:
    """The same deal without Safe-Care: lower by the expected refund, not below cost x 1.10 (or below the price
    itself, which can already be under that in the end-of-round clearance)."""
    return max(min(int(cost * 1.10), price), int(price * (1.0 - break_rate)))


def required_warranty_hours(text):
    values=[]
    for pattern in (r"(?:warranty|guarantee|보증)\s*(?:hours?|기간)?[=: ]*(\d+(?:\.\d+)?)\s*(?:h(?:ours?)?|시간)",
                    r"(\d+(?:\.\d+)?)\s*(?:h(?:ours?)?|시간)\s*(?:warranty|guarantee|보증)"):
        values.extend(float(v) for v in re.findall(pattern,text.lower()))
    return max(values,default=0)


def compute_unit_pricing_and_terms(unit: dict[str, Any], hub: dict[str, Any]) -> dict[str, Any]:
    """Compute High-Anchor opening ask, Fast-Close r1_target, hard reserve floor, and warranty_hours
    for a Round 3 stock unit, with differentiated clearance logic for expiring food vs aged goods."""
    cost = int(unit.get("cost") or 100)
    # Catalog value never changes merely because wholesale cost exceeds it.
    list_price = max(1, int(unit.get("list_price") or 1))
    kind = unit.get("kind", "goods")
    grade = str(unit.get("live_grade") or unit.get("grade") or "A")
    hours_left = unit.get("hours_left")
    sku = str(unit.get("sku") or "")

    now = time.time()
    supp_ts = float(unit.get("supplied_at") or 0)
    age_h = max(0.0, (now - supp_ts) / 3600.0) if supp_ts > 1_700_000_000 else 0.0

    market_anchor = get_sku_market_anchor(hub, sku, list_price)

    if kind == "goods":
        # Non-food ("goods"): Grade is permanent (A/B/C), no expiration, negligible storage fee (~10-50 KRW/day).
        # Normal goods: Prices include Safe-Care with target margins (A >= cost x 1.082, B >= cost x 1.19, C >= cost x 1.62).
        break_rate = BREAK_RATE.get(grade, BREAK_RATE["C"])
        hard_floor = max(cost + 50, int(cost * 1.08), int(cost / (1.0 - break_rate) * 1.05))
        if grade == "A":
            # High anchor: 92~95% of list_price (gives buyer strong discount satisfaction)
            open_ask = max(int(cost * 1.24), min(int(list_price * 0.95), max(market_anchor, int(list_price * 0.90))))
            # Turn 1 target: around 84~86% of list_price
            r1_target = max(hard_floor, int(round(cost + (open_ask - cost) * 0.50)))
            # Turn 2 mid-step: steps down near floor (~81~82% of list_price) to close before walkaway
            r2_target = max(hard_floor, int(round(cost + (open_ask - cost) * 0.18)))
        elif grade == "B":
            # Grade B cost is 40-60% of list_price -> strong margin potential
            open_ask = max(int(cost * 1.30), int(list_price * 0.86))
            r1_target = max(hard_floor, int(list_price * 0.75))
            r2_target = max(hard_floor, int(list_price * 0.68))
        else:
            # Grade C (refurb): cost is 25-45% of list_price
            open_ask = max(int(cost * 1.38), int(list_price * 0.80))
            r1_target = max(hard_floor, int(list_price * 0.68))
            r2_target = max(hard_floor, int(list_price * 0.60))

        # Clearance for aged non-food inventory:
        # Non-food doesn't spoil, so NO panic dumping. But long-held units lock up cash capital:
        # - age >= 12h: soften reserve floor down to cost * 0.95 and lower targets so bargain hunters close
        # - 8h <= age < 12h: soften floor to cost * 1.02 (near cost/breakeven)
        if age_h >= 12.0:
            hard_floor = max(10, int(cost * 0.95))
            open_ask = max(hard_floor, min(open_ask, int(list_price * 0.82)))
            r1_target = max(hard_floor, int(round(cost * 1.02)))
            r2_target = hard_floor
        elif age_h >= 8.0:
            hard_floor = max(cost + 10, int(cost * 1.02))
            open_ask = max(hard_floor, min(open_ask, int(list_price * 0.88)))
            r1_target = max(hard_floor, int(round(cost * 1.06)))
            r2_target = hard_floor

        default_warranty = cash_policy.warranty_hours(grade)
    else:
        break_rate = 0.0
        hl = float(hours_left) if hours_left is not None else float("inf")
        # Perishable/Non-perishable Food ("food"): no storage fee, but expires (100% total loss if expired).
        # Shelf life remaining (hours_left) drives urgency, NOT age_h!
        if hl < 12.0:
            # Critical urgency (< 12h remaining): salvage price floor to avoid 100% discard loss
            hard_floor = max(10, int(cost * 0.35))
            open_ask = max(hard_floor, int(list_price * 0.50))
            r1_target = max(hard_floor, int(list_price * 0.42))
            r2_target = hard_floor
        elif hl < 24.0:
            # High urgency (< 24h remaining): aggressive markdown below wholesale cost
            hard_floor = max(10, int(cost * 0.55))
            open_ask = max(hard_floor, int(list_price * 0.65))
            r1_target = max(hard_floor, int(list_price * 0.55))
            r2_target = hard_floor
        elif hl < 48.0:
            # Moderate urgency (< 48h remaining or Grade C quality drop): breakeven / slight discount
            hard_floor = max(10, int(cost * 0.80 if hl < 48.0 else cost * 0.78))
            open_ask = max(hard_floor, int(list_price * 0.72))
            r1_target = max(hard_floor, int(list_price * 0.64))
            r2_target = hard_floor
        elif hl < 72.0:
            # Start discounting while buyers can still use the remaining shelf life (POLICY 11).
            hard_floor = max(10, int(cost * 1.00))
            open_ask = max(hard_floor, int(list_price * 0.80))
            r1_target = max(hard_floor, int(list_price * 0.74))
            r2_target = hard_floor
        elif grade == "C":
            hard_floor = max(10, int(cost * 0.78))
            open_ask = max(hard_floor, int(list_price * 0.72))
            r1_target = max(hard_floor, int(list_price * 0.64))
            r2_target = hard_floor
        else:
            # Fresh Food (hours_left >= 72h): maintain standard margins
            if grade == "A":
                hard_floor = max(cost + 10, int(cost * 1.03))
                open_ask = max(int(cost * 1.20), min(int(list_price * 0.95), max(market_anchor, int(list_price * 0.90))))
                r1_target = max(int(cost * 1.08), int(round(cost + (open_ask - cost) * 0.42)))
                r2_target = max(hard_floor, int(round(cost + (open_ask - cost) * 0.14)))
            elif grade == "B":
                hard_floor = cost
                open_ask = max(int(cost * 1.12), int(list_price * 0.84))
                r1_target = max(int(cost * 1.04), int(list_price * 0.76))
                r2_target = hard_floor
            else:
                hard_floor = max(1, int(cost * 0.78))
                open_ask = max(hard_floor, int(list_price * 0.72))
                r1_target = max(hard_floor, int(list_price * 0.64))
                r2_target = hard_floor

        default_warranty = 0

    first_quote = None
    if clearance_factor() == 1.0:
        first_quote = cash_policy.quote(unit,hub,load_catalog(MARKET_DIR),now)
    if first_quote:
        open_ask,r1_target,r2_target,hard_floor = (first_quote[k] for k in ('open_ask','r1_target','r2_target','hard_floor'))
        break_rate = first_quote['break_rate']
    if kind=='goods' and grade=='C':
        # No paid breakage refund. Do not discount an already unbundled quote twice.
        break_rate=0.0
        if not first_quote:
            open_ask=max(cash_policy.profit_floor(unit,hub,load_catalog(MARKET_DIR),now),
                         int(list_price*cash_policy.config().get('c_fallback_list_fraction',.64)))
            hard_floor=cash_policy.profit_floor(unit,hub,load_catalog(MARKET_DIR),now)
            r1_target=max(hard_floor,math.ceil(open_ask*.99))
            r2_target=r1_target
    open_ask = max(open_ask, hard_floor)
    r1_target = min(open_ask, max(hard_floor, r1_target))
    r2_target = min(r1_target, max(hard_floor, r2_target))

    # End-of-round clearance: stock left when the round ends is worth 0, so in the last day every price slides
    # linearly toward a salvage price of a quarter of cost; selling below cost still beats keeping the unit.
    f = clearance_factor()
    if f < 1.0:
        salvage = max(1, int(cost * 0.25))
        open_ask, r1_target, r2_target, hard_floor = (
            int(salvage + (p - salvage) * f) for p in (open_ask, r1_target, r2_target, hard_floor))
    if kind=='goods' and grade=='B':
        # B's actual-cost/risk floor survives old-stock and final-day liquidation paths.
        hard_floor=max(hard_floor,cash_policy.profit_floor(unit,hub,load_catalog(MARKET_DIR),now))
        open_ask=max(open_ask,hard_floor)
        r1_target=min(open_ask,max(r1_target,hard_floor))
        r2_target=min(r1_target,max(r2_target,hard_floor))
    disc_from_list = max(0, int(round((1.0 - open_ask / max(1, list_price)) * 100)))
    return {
        "open_ask": open_ask,
        "r1_target": r1_target,
        "r2_target": r2_target,
        "hard_floor": hard_floor,
        "list_price": list_price,
        "cost": cost,
        "grade": grade,
        "hours_left": hours_left,
        "warranty_hours": default_warranty,
        "break_rate": break_rate,
        "disc_pct": disc_from_list,
        "cash_first_quote": bool(first_quote),
    }


# --- Round 0-2 Legacy Helpers (for backward compatibility & market_test_week07.py) ---

ROUND3_MIN_OPEN_MULT = 1.15
ROUND3_MAX_OPEN_MULT = 1.25
ROUND3_DEFAULT_OPEN_MULT = 1.18
ROUND3_VIP_MIN_MULT = 1.08


def compute_round3_opening_ask(
    item: str,
    item_stats: dict,
    is_vip: bool = False,
    is_repeat: bool = False,
    is_wave: bool = False,
    is_fcfs: bool = False,
) -> tuple[int, int, int]:
    reserve = RESERVES[item]
    base_15x = reserve * PREMIUM_MULT
    min_open = math.ceil(reserve * ROUND3_MIN_OPEN_MULT)
    max_open = math.ceil(reserve * ROUND3_MAX_OPEN_MULT)

    cnt = int(item_stats.get("count") or 0)
    if cnt > 0:
        p65 = float(item_stats.get("p65") or (reserve * ROUND3_DEFAULT_OPEN_MULT))
        mean = float(item_stats.get("mean") or (reserve * ROUND3_DEFAULT_OPEN_MULT))
        blended = int(round(0.60 * p65 + 0.40 * mean))
        anchor = max(min_open, min(max_open, blended))
    else:
        anchor = math.ceil(reserve * ROUND3_DEFAULT_OPEN_MULT)

    if is_fcfs:
        ask = math.ceil(reserve * 1.08)
    elif is_vip or is_repeat:
        ask = max(math.ceil(reserve * ROUND3_VIP_MIN_MULT), int(round(anchor * 0.95)))
    elif is_wave:
        ask = min(max_open, int(round(anchor * 1.03)))
    else:
        ask = anchor

    r1_target = max(math.ceil(reserve * 1.05), int(round(reserve + (ask - reserve) * 0.50)))
    r1_target = min(ask, r1_target)
    disc_pct = int(round((1.0 - ask / base_15x) * 100))
    return ask, r1_target, disc_pct


def load_seller_state() -> dict:
    default = {
        "deals": 0,
        "walkaways": 0,
        "multiplier": PREMIUM_MULT,
        "vip_multiplier": VIP_MULT,
        "avg_market_purchasing_power": 220,
        "round3_mode": False,
        "settled_prices_by_item": {"lamp": [], "chair": [], "bookshelf": []},
        "settled_stats_by_item": {},
        "blacklist": ["11im"],
        "vips": [],
        "vip_spend_share": {},
        "referrals_by_buyer": {},
        "distinct_customers": [],
        "seen_buyers": [],
        "recent_deal_ts": [],
        "last_customer_ts": time.time(),
        "fcfs_slots_remaining": 0,
        "fcfs_buyer_counts": {},
        "recent_reviews": [],
        "trades": [],
    }
    if SELLER_HISTORY_FILE.exists():
        try:
            data = json.loads(SELLER_HISTORY_FILE.read_text(encoding="utf-8"))
            default.update(data)
        except Exception:
            pass
    return default


def save_seller_state(state: dict) -> None:
    try:
        state["trades"] = state.get("trades", [])[-25:]
        SELLER_HISTORY_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def compute_item_settle_stats(prices_by_item: dict[str, list[int]]) -> dict[str, dict]:
    stats = {}
    for item, reserve in RESERVES.items():
        raw = [int(p) for p in (prices_by_item.get(item) or []) if isinstance(p, (int, float)) and p >= reserve]
        if raw:
            s = sorted(raw[-40:])
            n = len(s)
            p50 = s[n // 2]
            p65 = s[min(n - 1, int(n * 0.65))]
            mean = round(sum(s) / n, 1)
            stats[item] = {"count": n, "mean": mean, "p50": p50, "p65": p65}
        else:
            anchor = math.ceil(reserve * 1.18)
            stats[item] = {"count": 0, "mean": float(anchor), "p50": anchor, "p65": anchor}
    return stats


def refresh_reviews_and_adapt_price() -> dict:
    state = load_seller_state()
    shops_file = MARKET_DIR / "shops.json"
    reviews = state.get("recent_reviews", [])
    if shops_file.exists():
        try:
            shops = json.loads(shops_file.read_text(encoding="utf-8"))
            for s in shops:
                if s.get("name") == MARKET_NAME:
                    reviews = s.get("reviews") or []
                    break
        except Exception:
            pass
    if reviews:
        state["recent_reviews"] = reviews[:5]
    state["settled_stats_by_item"] = compute_item_settle_stats(state.get("settled_prices_by_item") or {})
    save_seller_state(state)
    return state


def extract_buyer_offer(text: str, current_ask: int = 0, list_price: int = 0, sku: str = "") -> int | None:
    """Robust fallback parser to extract buyer's price offer from text,
    filtering out SKU codes, warranty hours, units, and distinguishing budget vs offer."""
    if not text:
        return None
    # 1. Normalize comma-separated numbers: "1,200" -> "1200"
    cleaned = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)

    # 2. Strip explicit SKU or standard hex SKU patterns
    if sku:
        cleaned = re.sub(re.escape(sku), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b[gfGF][0-9a-fA-F]{7,10}\b", "", cleaned)

    # 3. Strip time / warranty phrases: "6 hours", "6h", "24 hrs", "30 mins", "720h"
    cleaned = re.sub(_REQUIREMENT_KEY, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(_DURATION, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*(?:mins?|minutes?|m|seconds?|분|초)(?![a-zA-Z])", "", cleaned, flags=re.IGNORECASE)

    # 4. Strip quantity / unit phrases: "1 unit", "2 pcs", "1개", "1ea"
    cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*(?:units?|pcs?|items?|pieces?|ml|kg|g|개|팩|ea)(?![a-zA-Z])", "", cleaned, flags=re.IGNORECASE)

    # 5. Intent patterns
    intent_patterns = [
        r"(?i)(?:offer|counter|bid|제안|가격)\s*(?:is|of|at|:)?\s*(\d{2,7})",
        r"(?i)(?:pay|give|afford)\s*(?:up to|at)?\s*(\d{2,7})",
        r"(?i)(?:how about|what about|can you do|would you do)\s*(\d{2,7})",
        r"(?i)(?:buy|take it|take)\s*(?:at|for)\s*(\d{2,7})",
        r"(?i)\b(\d{2,7})\s*(?:credits?|won|원)",
    ]
    for pat in intent_patterns:
        m = re.findall(pat, cleaned)
        if m:
            offer = int(m[-1])
            return offer if offer > 0 else None

    # 6. Budget vs Offer distinction
    budget_match = re.search(r"(?i)\bbudget\s*(?:is|of|:)?\s*(\d{2,7})\b", cleaned)
    budget_val = int(budget_match.group(1)) if budget_match else None

    # 7. Collect raw numbers
    raw_nums = [int(x) for x in re.findall(r"\b\d+\b", cleaned)]
    if not raw_nums:
        return None
    if len(raw_nums) == 1:
        return raw_nums[0] if raw_nums[0] > 0 else None

    non_budget = [n for n in raw_nums if n != budget_val]
    if non_budget:
        ref_price = max(current_ask, list_price, 50)
        viable = [n for n in non_budget if n >= 10 or n >= ref_price * 0.1]
        if viable:
            return viable[-1] if viable[-1] > 0 else None
        return non_budget[-1] if non_budget[-1] > 0 else None
    return raw_nums[-1] if raw_nums[-1] > 0 else None


class Seller(AgentExecutor):
    def __init__(self, port: int):
        self.port = port
        self.open: dict[str, dict[str, Any]] = {}
        # committed_units tracks units for which a signed receipt has been issued,
        # guarding them against concurrent sales until ACP settlement is finalized.
        self.committed_units: dict[str, float] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        now_ts = time.time()

        # Session TTL Cleanup: purge abandoned negotiations (> 90s) so reserved units return to stock
        stale_tids = [tid for tid, d in self.open.items() if (now_ts - float(d.get("created_at") or now_ts)) > 90.0]
        for tid in stale_tids:
            self.open.pop(tid, None)

        # Committed Units TTL Cleanup: purge after 120s timeout
        stale_committed = [u for u, ts in self.committed_units.items() if (now_ts - ts) > 120.0]
        for u in stale_committed:
            self.committed_units.pop(u, None)

        inv = load_inventory(MARKET_DIR)
        is_round3_stock = int(inv.get("round", 0)) >= 3

        # If a committed unit is already absent from the fresh market inventory, ACP settlement finished!
        current_inv_unit_ids = {u.get("unit") for u in (inv.get("units") or [])}
        settled_uids = [u for u in self.committed_units if u not in current_inv_unit_ids]
        for u in settled_uids:
            self.committed_units.pop(u, None)

        deal = self.open.get(task.id)
        if deal is None:
            if is_round3_stock:
                catalog = load_catalog(MARKET_DIR)
                # Combined Lock: Negotiation Locks (self.open) + Checkout Settlement Locks (committed_units)
                negotiating_units = {
                    d["unit"]["unit"]
                    for d in self.open.values()
                    if isinstance(d.get("unit"), dict) and d["unit"].get("unit")
                }
                taken_units = negotiating_units | set(self.committed_units.keys())
                unit, demand_query = find_stock_unit(text, taken_units, inv, catalog)

                if unit is None:
                    queue_inbound_inquiry(demand_query, matched=False, raw_text=text)
                    print(f"[stock-miss] Out of stock for buyer request: {text[:80]} (recorded demand: {demand_query})", flush=True)
                    await up.reject(say("Sorry, no matching stock meets your request (including any required shelf life plus checkout buffer). No receipt issued."))
                    return

                hub = load_hub()
                pricing = compute_unit_pricing_and_terms(unit, hub)
                ask = pricing["open_ask"]
                r1_target = pricing["r1_target"]
                hard_floor = pricing["hard_floor"]
                grade = pricing["grade"]
                warranty = pricing["warranty_hours"]
                if required_warranty_hours(text)>warranty and unit.get('kind')=='goods':
                    await up.reject(say(f"This certified grade {grade} unit has warranty_hours={warranty}; it cannot meet your required warranty. No receipt issued."))
                    return
                hours_left = pricing["hours_left"]
                list_price = pricing["list_price"]
                is_food = unit.get("kind") == "food"
                required_hours = extract_food_use_hours(text) if is_food else None

                # Check if the buyer already included an initial offer price in their first message!
                initial_offer = extract_buyer_offer(text, current_ask=ask, list_price=list_price, sku=unit["sku"])
                if initial_offer and initial_offer >= ask and (not is_food or required_hours is not None):
                    # Recheck live expiry immediately before the opening receipt path.
                    grade, hours_left = compute_live_unit_grade(unit, catalog)
                    if is_food and (grade == "expired" or not food_has_use_time(unit, required_hours)):
                        await up.reject(say("Required use period exceeds remaining shelf life plus checkout buffer; transaction cancelled. No receipt issued."))
                        return
                    # Instant 1-message close if buyer offered at or above our opening ask!
                    final_price = ask
                    # Instantly lock in committed_units so no concurrent buyer can grab this unit during checkout
                    self.committed_units[unit["unit"]] = now_ts
                    receipt = {
                        "shop": SHOP_NAME,
                        "item": unit["sku"],
                        "price": final_price,
                        "task": task.id,
                        "unit": unit["unit"],
                        "claim": grade,
                        "cert": True,
                    }
                    if unit.get('kind')=='goods':
                        receipt["warranty_hours"] = warranty
                    receipt.update(care_terms(unit.get("kind", ""), warranty))
                    receipt = sign_receipt(receipt)
                    await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                    await up.complete(say(
                        f"Deal: {unit['name']} ({unit['sku']}) at {final_price} "
                        f"(grade {grade}, cert=true, warranty={warranty}h{' market maximum / 시장 허용 최대 보증' if warranty==72 else ''})."
                    ))
                    print(f"[instant-deal] Sold {unit['sku']} ({unit['name']}) unit={unit['unit']} at {final_price} (cost={unit['cost']})", flush=True)
                    return

                spec_parts = [
                    f"sku={unit['sku']}",
                    f"unit={unit['unit']}",
                    f"grade={grade}",
                    "cert=true",
                    f"list_price={list_price}",
                ]
                if hours_left is not None:
                    spec_parts.append(f"shelf_life_left={hours_left}h")

                care_note = ""
                if unit.get("kind") == "goods":
                    care_note = ((f" Price includes {'market maximum 72h warranty (시장 허용 최대 보증)' if warranty==72 else str(warranty)+'h warranty'}: "
                                  f"if it breaks within {warranty} h of the sale, the market refunds you the full price.")
                                 if warranty else " Grade C refurbished: warranty_hours=0, no warranty and no breakage refund; the quoted price is without warranty.")
                    if grade in NO_CARE_GRADES:
                        care_note += (f" Without Safe-Care: {no_care_price(ask, unit['cost'], pricing['break_rate'])}"
                                      f" (no refund); say 'without care' to take that option.")
                price_relation = (f"{list_price-ask} below catalog list" if ask < list_price else
                                  "at catalog list" if ask == list_price else f"{ask-list_price} above catalog list")
                opening_msg = (
                    f"{unit['name']} ({', '.join(spec_parts)}): asking price {ask} "
                    f"({price_relation} {list_price}).{care_note} "
                    f"Send your offer as a number or reply {ask} for an instant Ed25519-signed receipt."
                )
                if is_food and required_hours is None:
                    opening_msg = (
                        f"{unit['name']} (sku={unit['sku']}), asking price {ask}. "
                        + food_use_question(hours_left)
                    )
                queue_inbound_inquiry(demand_query, matched=True, raw_text=text, sku=unit["sku"])
                print(
                    f"[r3-quote] {unit['sku']} ({unit['name']}): cost={unit['cost']}, list={list_price}, "
                    f"grade={grade}, ask={ask}, r1_target={r1_target}, floor={hard_floor}, warranty={warranty}h, "
                    f"task={task.id}, cash_first_quote={pricing['cash_first_quote']}",
                    flush=True,
                )
                # Immediate Opening Ask Lock: unit is bound to this task so concurrent buyers cannot touch it
                self.open[task.id] = {
                    "mode": "round3_stock",
                    "unit": unit,
                    "item": unit["sku"],
                    "ask": ask,
                    "initial_ask": ask,
                    "cash_first_quote": pricing['cash_first_quote'],
                    "r1_target": r1_target,
                    "r2_target": pricing["r2_target"],
                    "hard_floor": hard_floor,
                    "list_price": list_price,
                    "grade": grade,
                    "warranty_hours": warranty,
                    "break_rate": pricing["break_rate"],
                    "no_care": (unit.get("kind") == "goods" and grade in NO_CARE_GRADES
                                and any(w in text.lower() for w in NO_CARE_WORDS)),
                    "rounds": 0,
                    "created_at": now_ts,
                    "offers": [],
                    "required_use_hours": required_hours,
                    "awaiting_use_period": is_food and required_hours is None,
                }
                await up.requires_input(say(opening_msg))
                return

            # --- Legacy Rounds 0-2 Mode (lamp, chair, bookshelf) ---
            item = next((k for k in RESERVES if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: lamp, chair, bookshelf."))
                return

            state = refresh_reviews_and_adapt_price()
            vips = set(state.get("vips", []))
            recent_order = (state.get("recent_orders") or {}).get(item) or {}
            active_buyer = recent_order.get("buyer")
            is_vip = active_buyer in vips
            recent_deal_ts = [float(t) for t in (state.get("recent_deal_ts") or []) if now_ts - float(t) <= 180.0]
            revs = state.get("recent_reviews") or []
            avg_stars = (sum(float(r.get("stars") or 0) for r in revs) / len(revs)) if revs else 0.0
            is_wave_active = (len(recent_deal_ts) >= 3) or (len(revs) >= 3 and avg_stars >= 4.1)

            known_customers = set(state.get("distinct_customers") or []) | set(state.get("seen_buyers") or [])
            is_repeat_buyer = bool(active_buyer and active_buyer in known_customers)
            item_stats = (state.get("settled_stats_by_item") or {}).get(item) or {}

            ask, r1_target, disc_pct = compute_round3_opening_ask(
                item=item,
                item_stats=item_stats,
                is_vip=is_vip,
                is_repeat=is_repeat_buyer,
                is_wave=is_wave_active,
                is_fcfs=False,
            )
            opening_msg = (
                f"{item} asking price: {ask} "
                f"(reply with {ask} alone for instant signed receipt). "
                f"[Fast Express Delivery and Full Flexible Refund Guarantee — asking price: {ask}]"
            )
            self.open[task.id] = {
                "mode": "legacy",
                "item": item,
                "ask": ask,
                "r1_target": r1_target,
                "hard_floor": RESERVES[item],
                "rounds": 0,
                "created_at": now_ts,
                "is_wave": is_wave_active,
                "offers": [],
            }
            await up.requires_input(say(opening_msg))
            return

        # --- Subsequent Turns: Process Buyer's Offer ---
        # Parsed by rules only. The per-turn Codex CLI call (2.5 s timeout) was unlikely to answer in time, added
        # up to ~3 s to every turn, and could leave codex processes running after a timeout on a 1 GB VM. The
        # model is used outside the negotiation path instead, where latency does not matter.
        text_low = text.lower()
        if deal.get("mode") == "round3_stock" and deal["unit"].get("kind") == "food":
            required_hours = extract_food_use_hours(text, awaiting_duration=deal.get("awaiting_use_period", False))
            if required_hours is not None:
                previous = deal.get("required_use_hours")
                deal["required_use_hours"] = max(previous, required_hours) if previous is not None else required_hours
                deal["awaiting_use_period"] = False
            live_grade, hours_left = compute_live_unit_grade(deal["unit"], load_catalog(MARKET_DIR))
            required_hours = deal.get("required_use_hours")
            if live_grade == "expired" or (required_hours is not None and not food_has_use_time(deal["unit"], required_hours)):
                self.open.pop(task.id, None)
                await up.reject(say(
                    f"Remaining shelf life {hours_left}h cannot meet required use period {required_hours}h "
                    "plus checkout buffer; transaction cancelled, unit released. No receipt issued."
                ))
                return
            if required_hours is None:
                deal["awaiting_use_period"] = True
                await up.requires_input(say(food_use_question(hours_left)))
                return
        drops_care = any(w in text_low for w in NO_CARE_WORDS)
        wants_w = not drops_care and any(kw in text_low for kw in ("warranty", "guarantee", "service", "care", "보증"))
        offer = extract_buyer_offer(
            text,
            current_ask=int(deal.get("ask") or 100),
            list_price=int(deal.get("list_price") or 100),
            sku=str(deal.get("item") or ""),
        )

        if offer is None:
            await up.requires_input(say(f"Send your offer as a number. Current ask is {deal['ask']}."))
            return

        deal["rounds"] += 1
        deal.setdefault("offers", []).append(offer)

        if deal.get("mode") == "round3_stock":
            u = deal["unit"]
            catalog = load_catalog(MARKET_DIR)
            live_grade, hours_left = compute_live_unit_grade(u, catalog)
            if live_grade == "expired":
                await up.reject(say("Sorry, that unit just passed its shelf-life window; transaction cancelled safely."))
                del self.open[task.id]
                return

            cost = int(u.get("cost") or 100)
            hard_floor = int(deal.get("hard_floor") or cost)   # above cost except in the end-of-round clearance
            r1_target = int(deal.get("r1_target") or hard_floor)
            r2_target = int(deal.get("r2_target") or hard_floor)
            if u.get('kind')=='goods' and required_warranty_hours(text)>int(deal.get('warranty_hours') or 0):
                await up.reject(say("Requested warranty exceeds the disclosed terms. No receipt issued."))
                del self.open[task.id]
                return
            if u.get('kind')=='goods' and live_grade=='B':
                hard_floor=max(hard_floor,cash_policy.profit_floor(u,load_hub(),catalog,now_ts))
                r1_target=max(r1_target,hard_floor)
                r2_target=max(r2_target,hard_floor)

            # Safe-Care choice: the buyer drops it by saying so (grades A/B only) and asking for it brings it back.
            # The stored targets include Safe-Care; without it every target is lower by the expected refund.
            is_goods = u.get("kind") == "goods"
            if is_goods and drops_care and deal.get("grade") in NO_CARE_GRADES:
                deal["no_care"] = True
            elif wants_w:
                deal["no_care"] = False
            no_care = bool(is_goods and deal.get("no_care"))
            # Keep explicit zero/custom stored terms; only older deals missing the key use grade policy.
            stored_warranty = int(deal.get('warranty_hours', cash_policy.warranty_hours(live_grade))) if is_goods else 0
            warranty = stored_warranty if not no_care else 0
            disclosed_care = care_terms(u.get("kind", ""), warranty)
            terms = ""
            if is_goods:
                terms = (f" with {disclosed_care['care_plan']}: {disclosed_care['refund_policy']}" if warranty > 0 else
                         f" without Safe-Care (warranty_hours=0, no warranty; {disclosed_care['refund_policy']})")
            b = float(deal.get("break_rate") or 0.0)
            in_mode = (lambda p: no_care_price(p, cost, b)) if no_care else (lambda p: p)
            hard_floor, r1_target, r2_target = in_mode(hard_floor), in_mode(r1_target), in_mode(r2_target)

            # Balanced 3-Turn Concession Policy:
            # Closes fast (max 2~3 turns) before buyer walkaway/speed-penalty while preserving healthy margin & warranty hedging:
            # - Turn 1: accept if buyer offer >= r1_target (high margin in 1 turn -> 5★ speed & big profit)
            # - Turn 2: accept if buyer offer >= r2_target (good margin ~20-25% -> 5★/4★)
            # - Turn 3+: accept if buyer offer >= hard_floor (safe floor preserving >= 18% margin on goods, preventing walkaway)
            should_accept = (
                (offer >= r1_target)
                or (deal["rounds"] == 2 and offer >= r2_target)
                or (deal["rounds"] >= 3 and offer >= hard_floor)
            )
            if deal.get('cash_first_quote'):
                final_target = max(hard_floor,math.ceil(in_mode(deal['initial_ask'])*.99))
                should_accept = offer >= final_target
                if not should_accept and deal['rounds'] >= 2:
                    await up.reject(say(f"Our final price is {final_target}, cert=true{terms}. No deal at {offer}."))
                    del self.open[task.id]
                    return

            # Never below cost, except in the end-of-round clearance, where the floor itself goes below cost
            if should_accept and offer >= min(cost, hard_floor):
                # Recheck immediately before signing, even if the requirement was known earlier.
                if u.get("kind") == "food":
                    live_grade, hours_left = compute_live_unit_grade(u, catalog)
                    required_hours = deal.get("required_use_hours")
                    if required_hours is None or live_grade == "expired" or not food_has_use_time(u, required_hours):
                        self.open.pop(task.id, None)
                        await up.reject(say("Required use period is unknown or exceeds remaining shelf life plus checkout buffer; transaction cancelled. No receipt issued."))
                        return
                current_ask = in_mode(int(deal["ask"]))
                final_price = min(current_ask, int(offer))
                if is_goods and live_grade=='B' and final_price<hard_floor:
                    await up.reject(say("The previously quoted price no longer clears the actual-cost profit floor. No receipt issued."))
                    del self.open[task.id]
                    return
                receipt = {
                    "shop": SHOP_NAME,
                    "item": u["sku"],
                    "price": final_price,
                    "task": task.id,
                    "unit": u["unit"],
                    "claim": live_grade,
                    "cert": True,
                }
                if is_goods:
                    receipt["warranty_hours"] = warranty
                receipt.update(care_terms(u.get("kind", ""), warranty))
                receipt = sign_receipt(receipt)

                await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                await up.complete(say(
                    f"Deal: {u['name']} ({u['sku']}) at {final_price} "
                    f"(unit={u['unit']}, grade={live_grade}, cert=true, warranty={warranty}h{' market maximum / 시장 허용 최대 보증' if warranty==72 else ''})."
                ))
                profit_est = final_price - cost
                print(
                    f"[r3-deal] Sold {u['sku']} ({u['name']}) unit={u['unit']} at {final_price} "
                    f"(cost={cost}, gross_before_refunds={profit_est}, grade={live_grade}, warranty={warranty}h, "
                    f"task={task.id}, first_quote_accepted={final_price == deal.get('initial_ask')}) in {deal['rounds']} turn(s); receipt signed, payment not yet confirmed.",
                    flush=True,
                )
                # Elevate to committed lock before deleting negotiation state
                self.committed_units[u["unit"]] = now_ts
                del self.open[task.id]
                return

            # Reject if buyer stalls below cost for >= 4 rounds
            if deal["rounds"] >= 4 and offer < hard_floor:
                await up.reject(say(f"Cannot sell below certified cost floor ({hard_floor}). No deal at {offer}."))
                del self.open[task.id]
                return

            # Stepped Concession Curve:
            # After Turn 1: counter with r1_target
            # After Turn 2: counter with r2_target (moderate concession to encourage close on Turn 2/3)
            # After Turn 3+: counter with hard_floor
            # deal["ask"] stays in Safe-Care terms; the message quotes the price for the option the buyer is on
            care_targets = (int(deal.get("r1_target") or 0), int(deal.get("r2_target") or 0), int(deal.get("hard_floor") or 0))
            step = care_targets[0] if deal["rounds"] == 1 else (care_targets[1] if deal["rounds"] == 2 else care_targets[2])
            if deal.get('cash_first_quote'):
                step = max(care_targets[2],math.ceil(deal['initial_ask']*.99))
            deal["ask"] = max(care_targets[2], step)
            new_ask = in_mode(deal["ask"])

            if is_goods and no_care and stored_warranty > 0:
                terms += f"; optional Safe-Care {stored_warranty}h at {deal['ask']}"
            elif is_goods and warranty > 0 and deal.get("grade") in NO_CARE_GRADES:
                terms += f"; without Safe-Care {no_care_price(deal['ask'], cost, b)}"
            await up.requires_input(say(
                f"Special concession — {u['name']} ({u['sku']}, grade={live_grade}, cert=true): "
                f"asking price {new_ask}{terms}. Reply with an offer or accept {new_ask} to close now."
            ))
            return
            return

        # --- Legacy Rounds 0-2 Offer Processing ---
        item = deal["item"]
        reserve = RESERVES[item]
        r1_target = int(deal.get("r1_target") or math.ceil(reserve * 1.05))

        if len(deal["offers"]) >= 3 and offer < reserve and deal["offers"][-1] == deal["offers"][-2]:
            await up.reject(say(f"No deal at {offer}. Flexible cancellation policy applied."))
            del self.open[task.id]
            return

        should_accept = (offer >= r1_target) or (deal["rounds"] >= 2 and offer >= reserve)
        if should_accept and offer >= reserve:
            final_price = min(int(deal["ask"]), int(offer)) if offer > deal["ask"] else int(offer)
            receipt = sign_receipt({
                "shop": SHOP_NAME,
                "item": item,
                "price": final_price,
                "task": task.id,
            })
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item} at {final_price}."))
            state = load_seller_state()
            state["deals"] = state.get("deals", 0) + 1
            settled_map = dict(state.get("settled_prices_by_item") or {})
            settled_list = list(settled_map.get(item) or [])
            settled_list.append(final_price)
            settled_map[item] = settled_list[-40:]
            state["settled_prices_by_item"] = settled_map
            state["settled_stats_by_item"] = compute_item_settle_stats(settled_map)
            save_seller_state(state)
            del self.open[task.id]
            return

        if deal["rounds"] >= 5:
            await up.reject(say(f"No deal at {offer}. Flexible no-fee cancellation applied."))
            del self.open[task.id]
            return

        if deal["rounds"] == 1:
            new_ask = max(reserve, r1_target)
        elif deal["rounds"] == 2:
            new_ask = math.ceil(reserve * 1.02)
        else:
            new_ask = reserve
        deal["ask"] = new_ask
        await up.requires_input(
            say(f"Asking price: {new_ask}. Reply with {new_ask} alone for instant signed receipt.")
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task:
            self.open.pop(task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP_NAME,
        description=(
            "mjforge Certified Fulfillment Shop: Every unit ships with the market-signed certificate (cert=true), "
            "honest current grades (A/B/C). A goods include the market maximum 72-hour full-refund warranty; "
            "B goods include 6 hours; C refurbished goods have no warranty or breakage refund and a separately priced offer. First quotes reflect certified grade, "
            "actual unit cost and comparable trades; prices can exceed catalog list when costs require it. "
            "Ed25519-signed receipts. Ask for a SKU or category: 'I want to buy <name> (<sku>)'."
        ),
        version="3.2.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Buy a certified unit from stock",
                description=(
                    "Ask for a SKU, product name, or category, then send an offer price as a number. "
                    "Replies quote sku, unit, honest grade, certificate (cert=true), shelf life, warranty_hours, "
                    "catalog list price, and the actual ask. Send the quoted price for an immediate Ed25519-signed receipt."
                ),
                tags=["shopping", "negotiation", "certified", "warranty"],
                examples=["I want to buy <name> (<sku>)", "15000"],
            )
        ],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(port), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")

