"""Accept an ask above the recent-price cap when it still leaves the value margin. Off unless the setting says otherwise. No model call.

The cap normally comes from the value margin, and a recent settlement can pull it lower. This lifts only that second limit: the ask must be goods of a
quoted A or B grade, at or under 80% of list, inside the order budget and equal to the seller's current ask. Payment still has to pass the receipt check and the kit's mandate limit.
Deals taken this way are kept with their numbers. If their realized surplus adds up below zero, it switches itself off and says so.
A new `resume_token` in the setting starts a new count after such a stop.
"""

from datetime import datetime, timezone
from fractions import Fraction
import json
import math
from pathlib import Path
import time

from market_state import read, write

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "buyer_support.json"
STATE = "reference-relief.json"
ALERTS = "logs/reference-relief-alerts.jsonl"
REASON_CODE = "rule_reference_relief"
ALLOWED_AFTER = ("counter_already_used", "counter_ratio_exceeded")
GRADES = ("A", "B")
VALUE_SHARE = Fraction(4, 5)
CHECK_SECONDS = 300
REASON = "Clear ask over the recent-price cap, inside the value margin and the order budget; accepted as asked, no model used."


def settings(path=None):
    """The validated setting; anything missing, unreadable or malformed means off."""
    try:
        section = json.loads(Path(path or CONFIG).read_text(encoding="utf-8")).get("reference_relief")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(section, dict) or section.get("enabled") is not True or not isinstance(section.get("resume_token"), str) or not section["resume_token"]:
        return None
    return {"resume_token": section["resume_token"]}


def value_limit(list_price):
    if type(list_price) is bool or type(list_price) not in (int, float) or not math.isfinite(list_price) or list_price <= 0:
        return None
    return math.floor(Fraction(str(list_price)) * VALUE_SHARE)


def eligible(ask, *, cap, list_price, order_budget, grade, kind, catalog_kind, counter_reason):
    """(True, None) or (False, why). Every condition is checked; the seller's current ask is the only price this can ever return."""
    limit = value_limit(list_price)
    if counter_reason not in ALLOWED_AFTER:
        return False, "first_turn_counter_or_other_reason"
    if type(ask) is not int or type(cap) is not int or ask <= cap:
        return False, "not_over_cap"
    if kind != "goods" or catalog_kind != "goods":
        return False, "not_goods"
    if grade not in GRADES:
        return False, "grade_not_a_or_b"
    if limit is None or ask > limit:
        return False, "over_value_limit"
    if type(order_budget) is not int or ask > order_budget:
        return False, "over_order_budget"
    return True, None


def alert(root, message, details, now):
    print(f"[구매] {message}", flush=True)
    path = Path(root) / ALERTS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as stream:
        stream.write(json.dumps({"at": now, "message": message, **details}, ensure_ascii=False) + "\n")


def realized(root, orders):
    """Surplus of settled deals among these orders, breakage and refunds included. A deal not settled yet counts for nothing."""
    from buyer_surplus import settled_deals
    deals, unmatched = settled_deals(Path(root))
    picked = [d for d in deals if d["order"] in orders]
    return sum(d["estimated_surplus"] for d in picked), len(picked), sum(d["broken"] for d in picked), len(unmatched)


def active(md, root=ROOT, config_path=None, now=None):
    """The token when this path may run. Off on any doubt, and it never stops an order."""
    try:
        chosen = settings(config_path)
        if not chosen:
            return None
        now = time.time() if now is None else now
        md = Path(md)
        state = read(md / STATE, {})
        token = chosen["resume_token"]
        if (state.get("stopped") or {}).get("token") == token:
            return None
        orders = {d["order"] for d in state.get("deals", []) if d.get("token") == token}
        if orders and not 0 <= now-state.get("checked_at", 0) < CHECK_SECONDS:
            total, settled, broken, unmatched = realized(root, orders)
            state["checked_at"] = now
            state["realized"] = {"token": token, "surplus": total, "settled": settled, "broken": broken, "unmatched_breaks_in_market_data": unmatched}
            if total < 0:
                state["stopped"] = {"token": token, "at": now, **state["realized"]}
                write(md / STATE, state)
                alert(root, f"시세 상한 완화 자동 중지: 누적 실현 잉여 {total}, 정산 {settled}건, 고장 {broken}건", state["stopped"], now)
                return None
            write(md / STATE, state)
        return token
    except Exception:
        return None


def note_deal(md, token, order, taken, record, now=None):
    """Keep what the deal needs for later measurement. Failing here must not touch the order."""
    try:
        md = Path(md)
        state = read(md / STATE, {})
        state.setdefault("deals", []).append({"order": order, "token": token, "at": time.time() if now is None else now, **taken,
                                              "shop": record.get("shop_name"), "sku": record.get("item"), "category": record.get("category"), "outcome": record.get("outcome")})
        write(md / STATE, state)
    except Exception as error:
        print(f"[구매] 시세 상한 완화 기록 실패: {type(error).__name__}", flush=True)


def deals_by_order(md):
    """{order: what was noted}. Unreadable means none, so the ordinary path never depends on this file."""
    try:
        return {d["order"]: d for d in read(Path(md) / STATE, {}).get("deals", []) if d.get("order")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def public_rows_without_deals(md, rows):
    """Public rows minus our own relief deals. A settle row's seq is the ledger number on our payment, and it also names the unit, so the match is exact.

    Other buyers' deals are untouched, including any that later settle higher, which this cannot and should not tell apart.
    """
    deals = deals_by_order(md)
    if not deals:
        return rows
    ledgers, units = set(), set()
    from settlement_memory import payment_outcomes
    payments = payment_outcomes(Path(md) / "payments.jsonl")
    for order in deals:
        payment = payments.get(order) or {}
        if payment.get("paid") is True and type(payment.get("ledger")) is int:
            ledgers.add(payment["ledger"])
            unit = read(Path(md) / "buyer-deals" / f"{payment['ledger']}.json", {}).get("unit")
            if unit:
                units.add(unit)
    if not ledgers:
        return rows
    return [r for r in rows if not (r.get("kind") == "settle" and (r.get("seq") in ledgers or (r.get("data") or {}).get("unit") in units))]


def report(md, root=ROOT):
    """Read-only figures for this path."""
    md = Path(md)
    state = read(md / STATE, {})
    deals = state.get("deals", [])
    orders = {d["order"] for d in deals}
    total, settled, broken, unmatched = realized(root, orders) if orders else (0, 0, 0, 0)
    return {"at": datetime.now(timezone.utc).isoformat(), "enabled": settings() is not None, "stopped": state.get("stopped"), "taken": len(deals), "settled": settled, "broken": broken,
            "realized_surplus": total, "unmatched_breaks_in_market_data": unmatched, "deals": deals}


if __name__ == "__main__":
    print(json.dumps(report(ROOT / ".market"), ensure_ascii=False, indent=1))
