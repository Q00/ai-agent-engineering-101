"""After a failed talk, the next shop's open quote (Fable 11/15 권고 3, W-20261115-1530-AXtakeLLM경로).

best_quote closed every quote but the one it kept, so a talk that ended with no deal ended the order, though another
shop's open ask was one the talk could close (24 h to 11/15 16:4x: 652 of 1,972 goods talks with no deal, 101 of 195
food, had another quote within the take ceiling; review/W-20261115-1530-AXtakeLLM경로/nextreplay.py, a prediction).
On, best_quote keeps the best other quote open that fits (market_buyer.close_limit), is priced as a 6 h warranty
(not a short one or none: buyer_short_warranty.hours_for is None) and can go on as the trade (resumable); a talk
that ended cancelled, rejected or failed after at least one ask, with no receipt, early enough in the run (UNTIL), goes
on with it once: its own limit, margin cap and take worked out as main does for the shop it keeps, the oneshot opening
and buyer.talk's walk_take, and the same receipt checks after it (warranty, SKU, over_limit, hard_limits). Otherwise the
kept quote is cancelled. No study's treatment talks a second time (risk-SKU, take30, food floor windows).

Switch: .market/buyer_next_quote.json {"enabled": true, "kinds": ["goods", "food"]}; missing, unreadable or anything
else is off, and off is the order as before exactly (every other quote closed). BUYER_NEXT_QUOTE=0 is off too. Read
at every order (a buyer run is one order), so no restart either way.
"""
import json
import os

SWITCH = "buyer_next_quote.json"
UNTIL = 150             # seconds into the run: the kit stops market_buyer at 300 s, a talk takes 3-60 s
FAILED = ("TASK_STATE_CANCELED", "TASK_STATE_REJECTED", "TASK_STATE_FAILED")


def config(md, kind, env=None):
    """The switch's dict when it is on for this order kind, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_NEXT_QUOTE") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    kinds = d.get("kinds", ["goods", "food"])
    return d if isinstance(kinds, list) and kind in kinds else None


def failed_talk(out):
    """The talk ended with no deal on the shop's side or mine, after at least one ask: nothing can still close it."""
    return isinstance(out, dict) and out.get("state") in FAILED and out.get("receipt") is None \
        and any(who == "shop asked" for who, _ in out.get("history") or [])

# the order's study state of the first talk: kept under the row's next_quote.first.arms and reset for the second talk
FIRST_ARMS = ("_goods_ladder", "_food_ladder", "_b3", "_opening_discount_probe", "_goods_first_one_probe",
              "_one_won_default", "_goods_first_one_original", "_goods_first_one_price", "_moss_first", "_final_m1",
              "_sw_h_est")
