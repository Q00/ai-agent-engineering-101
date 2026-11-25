"""Food: a reserve with a margin over cost, not cost itself (W-20261109-2040-AX셀러전략).

Food never breaks, so its floor is cost x 1.0025 + 1 (profit_price.floor with b 0): a deal at the reserve keeps 0.25%.
11/09 10-22 h, 215 of our 463 grade A food deals closed at the reserve (B 222 of 850). On the same SKU x grade the
public ledger shows other shops selling higher (11/08 23:40-11/09 22:13, deal-weighted median price / list): A .739
(ours) vs .791, B .595 vs .658, C .481 vs .557, other shops higher on every one of the 4 SKUs per grade. Food is about a
third of our deals and no running study prices it (food_opening is disabled since 11/08; buyer_food_* are the buyer's).

Arm B raises the reserve to ceil(cost x (1 + share)) (0.05 by default, 0.02-0.10), never over list unless today's
reserve already is, never under today's; the ask rises to the new reserve when it is under it. Arm A keeps today's price.
Arms by unit, sha256("food-margin|salt|unit"), first byte odd is B: 4 SKUs carry 98% of our food deals (11/09 00-22 h,
3,098 of 3,173), so SKU arms would be two SKUs against two. Each unit ends sold, marked down or expired, so the verdict
is per unit supplied in the window, net of real cost with markdown sales and expiry losses in it; a markdown talk has
no record (it is never treated), so the verdict recomputes the unit's arm from salt and unit. Unit arms share a SKU, and the
bias has no fixed sign: a buyer turned away by a B unit can come back to an A unit (B looks worse), while B's higher
prices feed the SKU median and our paid prices that A's first ask reads (B - A shrinks towards zero), and when the cheap
A units are gone B units meet the buyers who would pay more, and B's leftovers share the clearance demand with half as
many units as a full rollout (B looks better). So a keep needs both: B - A per unit supplied above zero (cluster
interval), and no harm at shop level, food net per hour in the window not under the same hours before it. The verdict
also counts, per arm, B sales made while the SKU x grade had no A unit in stock and units cleared by markdown. The
first ask rises with the reserve when it is under it, so the treatment is the two together. Talks are recorded in both
arms (deal["food_margin"] -> shop_memory); the other seller studies' rows join it by task.
The margin study (sol_seller_margin, which hashes seller.py) is off since 11/09 17:46 (controller enabled false).

Eligible: food with a list price and a cost, no markdown (keep 1: near its date or the round's end the usual clearance
price holds, so a unit arm B did not sell is still cleared), not an ask-cap / scarce-ask / trial talk, not in the margin
study.

Switch: .market/seller_food_margin.json {"enabled": true, "study": "<id>", "salt": "<salt>", "share": 0.05,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS.
Quote order: SELLER_FOOD_MARGIN=0 keeps today's price; an active study uses A/B; otherwise default_off: true keeps
today's price; otherwise valid price.food_reserve_margin (0.02-0.15) applies B pricing as arm D; else today's price.
A missing switch allows D; an unreadable switch also allows D and warns once per process. Quotes never raise.
switch_on() preserves default_off and refuses to replace unreadable switches. A new salt/share needs a new study id.
To restore the default after default_off, as the seller's user (market), replace the switch with {"enabled": false}
or delete it once no study is needed; no restart. Remove the margin key to restore today's price.
Verdict: review/W-20261111-1938-AXPro주기/verdict-food-margin10.md.
Default .15 since 11/11 (food-margin15-1111, verdict-food-margin15.md)
Optional a_share (0.02-0.10) raises arm A too; both arms record a_share/b_share and their applied share.
With valid a_share, B must be higher and may reach 0.15; without it, A stays unchanged and B is capped at 0.10.
"""
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import profit_price

SWITCH = "seller_food_margin.json"
SHARE, SHARE_LOW, SHARE_HIGH = 0.05, 0.02, 0.10
STUDY_SHARE_HIGH = 0.15
DEFAULT_SHARE_HIGH = STUDY_SHARE_HIGH
MAX_WINDOW_HOURS = 20
_warned_invalid_default = False
_warned_unreadable_switch = False


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _share_ok(v):
    return _num(v) and SHARE_LOW <= v <= SHARE_HIGH


def _study_share_ok(d):
    share = d.get("share", SHARE)
    if "a_share" not in d:
        return _share_ok(share)
    return _share_ok(d["a_share"]) and _num(share) and d["a_share"] < share <= STUDY_SHARE_HIGH


def _read_switch(md):
    try:
        d = json.loads((Path(md) / SWITCH).read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None, "unreadable", "ValueError"
        return d, "readable", None
    except FileNotFoundError:
        return None, "missing", None
    except (OSError, ValueError) as e:
        return None, "unreadable", type(e).__name__


def config(md=None, env=None, now=None):
    """{"study", "salt", "share", "start_at", "stop_at"} with the switch on at now, else None."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("SELLER_FOOD_MARGIN") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
    d, _, _ = _read_switch(md)
    return _config_of(d, now)


def _config_of(d, now):
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt, share = d.get("study"), d.get("salt"), d.get("share", SHARE)
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _study_share_ok(d):
        return None
    start, stop = d.get("start_at"), d.get("stop_at")
    if not _window(start, stop) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "share": float(share), "start_at": start, "stop_at": stop,
            **({"a_share": float(d["a_share"])} if "a_share" in d else {})}


def switch_on(md, *, study, salt, start_at, hours, share=SHARE, a_share=None):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "start_at": start_at, "stop_at": stop_at}
    if a_share is not None:
        d["a_share"] = a_share
    if (not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _study_share_ok(d)
            or not _window(start_at, stop_at)):
        raise ValueError("seller_food_margin: bad switch")
    previous, status, error = _read_switch(md)
    if status == "unreadable":
        raise ValueError(f"seller_food_margin: existing switch unreadable ({error}); not replaced")
    if isinstance(previous, dict) and previous.get("default_off") is True:
        d["default_off"] = True
    return _write_switch(md, d)


def default_off(md):
    """Stop the default without restart, replacing the switch atomically (0600)."""
    return _write_switch(md, {"enabled": False, "default_off": True})


def _write_switch(md, d):
    path = Path(md) / SWITCH
    tmp = path.with_name(f"{SWITCH}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return d


def arm(cfg, unit):
    """"A" or "B" for a unit under the study's salt; None without a study or a unit id."""
    if not cfg or not isinstance(unit, str) or not unit:
        return None
    h = hashlib.sha256(f"food-margin|{cfg['salt']}|{unit}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def decide(u, q, *, reserve, ask, keep, margin_assignment, pricing=None, md=None, env=None, now=None):
    """(reserve, ask, record|None). u: the unit; q: profit_price.quote()'s result; reserve and ask: the talk's so far.
    Today's reserve and ask unless arm B raises them."""
    global _warned_invalid_default, _warned_unreadable_switch
    try:
        env = os.environ if env is None else env
        if str(env.get("SELLER_FOOD_MARGIN") or "").strip().lower() in ("0", "false", "no", "off"):
            return reserve, ask, None
        switch_dir = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
        switch, status, error = _read_switch(switch_dir)
        cfg = _config_of(switch, time.time() if now is None else now)
        if cfg is None:
            if status == "unreadable" and not _warned_unreadable_switch:
                print(f"SELLER_FOOD_MARGIN switch unreadable ({error}); default applies", file=sys.stderr)
                _warned_unreadable_switch = True
            if isinstance(switch, dict) and switch.get("default_off") is True:
                return reserve, ask, None
            price_cfg = profit_price.cfg_of(pricing)
            share = price_cfg.get("food_reserve_margin")
            if _num(share) and SHARE_LOW <= share <= DEFAULT_SHARE_HIGH:
                cfg = {"study": "default", "share": float(share), "stop_at": None}
            elif "food_reserve_margin" in price_cfg and not _warned_invalid_default:
                print("SELLER_FOOD_MARGIN invalid price.food_reserve_margin; keeping today's price", file=sys.stderr)
                _warned_invalid_default = True
        if (not cfg or not isinstance(u, dict) or u.get("kind") != "food" or not _num(u.get("list_price"))
                or u["list_price"] <= 0 or not _num(u.get("cost")) or u["cost"] <= 0
                or not _num(reserve) or not _num(ask) or not _num(keep) or keep < 1 or margin_assignment is not None
                or str(q.get("why") or "").startswith(("ask cap", "scarce ask", "trial "))
                or profit_price.ask_cap(profit_price.cfg_of(pricing), u) is not None):
            return reserve, ask, None
        a = "D" if cfg["stop_at"] is None else arm(cfg, u.get("unit"))
        if a is None:
            return reserve, ask, None
        lp = u["list_price"]
        new_reserve, new_ask = reserve, ask
        share = cfg["a_share"] if a == "A" and "a_share" in cfg else cfg["share"]
        if a in ("B", "D") or "a_share" in cfg:
            new_reserve = int(max(reserve, min(math.ceil(round(u["cost"] * (1 + share), 6)), lp)))
            new_ask = int(max(ask, new_reserve))
        return new_reserve, new_ask, {"study": cfg["study"], "arm": a, "share": share, "usual_reserve": reserve,
                                      "usual_ask": ask, "reserve": new_reserve, "ask": new_ask, "list": lp,
                                      "cost": u["cost"], "sku": u.get("sku"), "grade": u.get("grade"),
                                      "unit": u.get("unit"), "stop_at": cfg["stop_at"],
                                      **({"a_share": cfg["a_share"], "b_share": cfg["share"]} if "a_share" in cfg else {})}
    except Exception as e:  # noqa: BLE001 - a broken setting never stops a sale
        print(f"SELLER_FOOD_MARGIN skipped: {type(e).__name__}", file=sys.stderr)
        return reserve, ask, None
