"""Queue a bounded factual buyer report using the course outbox contract."""

from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import sys
import time

from market_state import read, write

ROOT = Path(__file__).resolve().parent
EARLIER_LIMITS = {"buyer_daily_limit": 12, "buyer_report_limit": 9, "shop_daily_limit": 4, "shop_reply_interval_seconds": 3600, "min_interval_seconds": 3600,
                  "brake_dislikes": 3, "brake_dislike_ratio": .25, "minimum_report_surplus_ratio": .1}
MARKET_SHOP_INTERVAL = 300
# Nearly every post on the board carries one dislike, so one says nothing; a shop slows down only when several posts draw two or more.
SHOP_BRAKE_DISLIKES, SHOP_BRAKE_POSTS = 2, 3
BUYER_CORRECTION = "buyer_correction"  # A later breakage or refund on a deal we already reported. The one buyer post a pause allows.
SKU_REPEAT = 86400
# Patch 3.4: a buyer's forum score counts one stance per account and author. A post needs time for votes, since nearly every new one starts with a single dislike.
BUYER_STOP_POSTS, BUYER_STOP_MIN_POSTS, BUYER_STOP_POST_AGE, BUYER_STOP_FORUM_DROP = 20, 5, 1800, .02
OWN_LOGIN = "kcwarni"


def valid_limits(value):
    try:
        interval = value.get("buyer_min_interval_seconds", 3600) if isinstance(value, dict) else None
        return (isinstance(value, dict) and 4 <= value["buyer_daily_limit"] <= 288 and 3 <= value["buyer_report_limit"] <= 288 and type(interval) is int and MARKET_SHOP_INTERVAL <= interval <= 3600
                and value["buyer_report_limit"] < value["buyer_daily_limit"] and type(value["shop_daily_limit"]) is int and 4 <= value["shop_daily_limit"] <= 24
                and type(value["shop_reply_interval_seconds"]) is int and 600 <= value["shop_reply_interval_seconds"] <= 3600
                and value["min_interval_seconds"] >= 3600 and value["brake_dislikes"] >= 3 and 0 < value["brake_dislike_ratio"] <= .5
                and .1 <= value["minimum_report_surplus_ratio"] <= 1)
    except (KeyError, TypeError):
        return False


def posting_limits(md, now):
    """A missing or unusable settings file means the earlier, smaller limits, never a stopped hook. Buyer posts run only on an explicit true."""
    try:
        value = read(ROOT / "forum_posting.json")
    except (OSError, ValueError):
        value = None
    if not valid_limits(value):
        value = dict(EARLIER_LIMITS)
    forum = read(md / "forum-intel.json", {})
    reactions = forum.get("summary", {}).get("own_post_reactions", []) if (
        forum.get("status") == "ok" and 0 <= now-forum.get("observed_at", 0) <= 3600) else []
    shop_brake = sum(r.get("role") == "shop" and (r.get("dislikes") or 0) >= SHOP_BRAKE_DISLIKES for r in reactions) >= SHOP_BRAKE_POSTS
    shop_limits = {"shop_daily_limit": EARLIER_LIMITS["shop_daily_limit"], "shop_reply_interval_seconds": EARLIER_LIMITS["shop_reply_interval_seconds"]} if shop_brake else {}
    return {**value, **shop_limits, "buyer_min_interval_seconds": value.get("buyer_min_interval_seconds", 3600), "buyer_resume_token": str(value.get("buyer_resume_token", "1")), "shop_reaction_brake": shop_brake,
            "buyer_posts_enabled": value.get("buyer_posts_enabled") is True}


def board_forum(md, now):
    snapshot = read(md / "market-intel.json", {})
    if snapshot.get("round") != 3 or not 0 <= now-snapshot.get("observed_at", 0) <= 3600:
        return None
    forum = ((snapshot.get("competition") or {}).get("own") or {}).get("buyers", {}).get("forum")
    return forum if type(forum) in (int, float) and math.isfinite(forum) else None


def buyer_stop_reason(md, resume, settings, now):
    """Why buyer posts must stop since `resume`, or None. Unknown data never stops them."""
    forum = board_forum(md, now)
    if resume.get("forum") is not None and forum is not None and round(resume["forum"]-forum, 6) >= BUYER_STOP_FORUM_DROP:
        return {"reason": "board_forum_fell", "forum_at_resume": resume["forum"], "forum_now": forum}
    posts = read(md / "forum-buffer.json", {}).get("posts") or []
    settled = sorted((p for p in posts if p.get("author") == OWN_LOGIN and p.get("role") == "buyer" and type(p.get("ts")) in (int, float) and resume["at"] <= p["ts"] <= now-BUYER_STOP_POST_AGE), key=lambda p: p["ts"])[-BUYER_STOP_POSTS:]
    likes, dislikes = sum(p.get("likes") or 0 for p in settled), sum(p.get("dislikes") or 0 for p in settled)
    if len(settled) >= BUYER_STOP_MIN_POSTS and dislikes >= settings["brake_dislikes"] and dislikes/max(1, likes+dislikes) > settings["brake_dislike_ratio"]:
        return {"reason": "dislike_share", "posts": len(settled), "likes": likes, "dislikes": dislikes}
    return None


def buyer_stopped(md, state, settings, now):
    """True once the stop rule has fired for this resume token. A new `buyer_resume_token` in the settings starts a new count. Says so once, in the terminal and a log."""
    token = settings["buyer_resume_token"]
    resume = state.get("buyer_resume")
    changed = False
    if not resume or resume.get("token") != token:
        resume = state["buyer_resume"] = {"token": token, "at": now, "forum": board_forum(md, now)}
        changed = True
    elif resume.get("forum") is None:
        resume["forum"] = board_forum(md, now)
        changed = resume["forum"] is not None
    if (state.get("buyer_stop") or {}).get("token") == token:
        return True
    reason = buyer_stop_reason(md, resume, settings, now)
    if reason:
        state["buyer_stop"] = {"token": token, "at": now, **reason}
        changed = True
        print(f"[포럼] buyer 글 자동 중지: {reason['reason']}", file=sys.stderr, flush=True)
        path = md.parent / "logs/forum-buyer-stop.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf8") as stream:
            stream.write(json.dumps({"at": now, "token": token, **reason}) + "\n")
    if changed:
        write(md / "forum-policy.json", state)
    return bool(reason)


def message_key(message):
    return hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def enqueue(md, message, key, kind, metadata=None, now=None):
    """Serialize our writers; official kit sends the existing outbox contract."""
    from strategy_controller import acquire, release
    md = Path(md)
    md.mkdir(parents=True, exist_ok=True)
    handle = acquire(md / "forum-policy.lock")
    if handle is None:
        return "busy"
    try:
        return _enqueue(md, message, key, kind, metadata or {}, time.time() if now is None else now)
    finally:
        release(handle)


def note_buyer_pause(md, state, kind, now):
    """Count every skipped buyer post; say so only when the pause begins."""
    pause = state.get("buyer_pause")
    if not pause:
        pause = {"since": now, "skipped": {}}
        print("[포럼] buyer 글 중단: 정정 글만 허용", file=sys.stderr, flush=True)
        path = md.parent / "logs/forum-buyer-pause.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf8") as stream:
            stream.write(json.dumps({"at": now, "buyer_posts_enabled": False}) + "\n")
    pause["skipped"][kind] = pause["skipped"].get(kind, 0) + 1
    state["buyer_pause"] = pause
    write(md / "forum-policy.json", state)


def _enqueue(md, message, key, kind, metadata, now):
    settings = posting_limits(md, now)
    state = read(md / "forum-policy.json", {"ledger_refs": [], "last_at": 0, "day": "", "count": 0})
    actions = read(md / "forum-actions.json", {})
    if key in actions or (kind == "buyer_report" and metadata["ledger"] in state["ledger_refs"]):
        return "already_queued"
    if message["as"] == "buyer":
        if not settings.get("buyer_posts_enabled") and kind != BUYER_CORRECTION:
            note_buyer_pause(md, state, kind, now)
            return "buyer_posts_paused"
        if settings.get("buyer_posts_enabled"):
            state.pop("buyer_pause", None)
            if buyer_stopped(md, state, settings, now) and kind != BUYER_CORRECTION:
                return "buyer_posts_stopped"
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    role = message["as"]
    limits = state if role == "buyer" else state.get("shop", {"last_at": 0, "day": "", "count": 0})
    count = limits["count"] if limits["day"] == day else 0
    limit = settings["buyer_daily_limit"] if role == "buyer" else settings["shop_daily_limit"]
    if role == "shop":
        # General posts wait an hour and replies less; any two shop posts stay apart by the market's own limit.
        reply = kind == "stock_reply"
        last_kind = limits.get("last_reply_at", 0) if reply else limits.get("last_general_at", limits["last_at"])
        apart = settings["shop_reply_interval_seconds"] if reply else settings["min_interval_seconds"]
        if settings.get("shop_reaction_brake"):
            last_kind = limits["last_at"]  # Slowed down: every shop post shares one interval again, as before.
        if count >= limit or now-last_kind < apart or now-limits["last_at"] < MARKET_SHOP_INTERVAL:
            return "rate_limited"
    elif count >= limit or now-limits["last_at"] < settings["buyer_min_interval_seconds"]:
        return "rate_limited"
    reports_today = sum(a["kind"] == "buyer_report" and
                        datetime.fromtimestamp(a["queued_at"], timezone.utc).date().isoformat() == day
                        for a in actions.values())
    if kind == "buyer_report" and reports_today >= settings["buyer_report_limit"]:
        return "rate_limited"  # Reserve buyer slots for mature follow-ups.
    with (md / "outbox.jsonl").open("a", encoding="utf8") as stream:
        stream.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
    if role == "shop":
        limits.setdefault("last_general_at", limits["last_at"])  # A post from before these fields counts as a general one.
        limits["last_reply_at" if kind == "stock_reply" else "last_general_at"] = now
    limits.update(last_at=now, day=day, count=count+1)
    if role == "shop":
        state["shop"] = limits
    if kind == "buyer_report":
        state["ledger_refs"] = (state["ledger_refs"]+[metadata["ledger"]])[-1000:]
    snapshot = read(md / "market-intel.json", {})
    own = snapshot.get("competition", {}).get("own", {}) if (
        snapshot.get("round") == 3 and 0 <= now-snapshot.get("observed_at", 0) <= 3600) else {}
    board_before = {"at": snapshot.get("observed_at"),
                    "visits": (own.get("shops") or {}).get("visits"),
                    "forum": (own.get("buyers") or {}).get("forum"), "deals": (own.get("shops") or {}).get("deals")}
    actions[key] = {"queued_at": now, "kind": kind, "role": role, "message_key": message_key(message),
                    "message": message, "board_before": board_before, **metadata}
    write(md / "forum-policy.json", state)
    write(md / "forum-actions.json", dict(list(actions.items())[-1000:]))
    return "queued"


def comparison_text(md, settled, price, now):
    """Same SKU/grade/warranty goods only; public prices are context, not self evidence."""
    from purchase_monitor import settlement_price
    import statistics
    snapshot = read(md / "market-intel.json", {})
    product = read(md / "catalog.json", {}).get(settled["sku"], {})
    if product.get("kind") != "goods" or snapshot.get("round") != 3 or not 0 <= now-snapshot.get("observed_at", 0) <= 3600:
        return " Comparable price sample unavailable; no cheapest-price claim."
    rows = {}
    for row in snapshot.get("rows", [])[-10000:]:
        data = row.get("data") or {}
        amount = settlement_price(data)
        if (row.get("round") == 3 and row.get("kind") == "settle"
                and type(row.get("seq")) is int and row["seq"] != settled["ledger"]
                and type(row.get("ts")) in (int, float) and 0 <= now-row["ts"] <= 86400
                and data.get("item") == settled["sku"] and data.get("grade") == settled.get("grade")
                and (data.get("warranty_hours") or 0) == (settled.get("warranty_hours") or 0) and amount is not None):
            rows[row["seq"]] = amount
    if len(rows) < 3:
        return " Comparable price sample below 3; no cheapest-price claim."
    median = statistics.median(rows.values())
    delta = (price / median - 1) * 100
    return (f" Observed same-SKU/grade/warranty goods sample n={len(rows)}, median={median:g}; "
            f"paid {abs(delta):.1f}% {'below' if delta < 0 else 'above'} sample median. "
            "Sample is incomplete; later refunds excluded.")


def queue_report(md, deal, result, now=None):
    now = time.time() if now is None else now
    settled, receipt = (deal.get(k) or {} for k in ("settled", "receipt"))
    ledger = settled.get("ledger")
    if result.get("stars") != 5 or type(ledger) is not int or ledger <= 0:
        return "insufficient_evidence"
    price, value = receipt.get("price"), settled.get("value")
    if (type(price) not in (int, float) or type(value) not in (int, float)
            or not math.isfinite(price) or not math.isfinite(value) or price <= 0 or value <= 0 or not settled.get("sku")):
        return "insufficient_evidence"
    md = Path(md)
    settings = posting_limits(md, now)
    if (value-price)/value < settings["minimum_report_surplus_ratio"]:
        return "insufficient_new_value"
    md.mkdir(parents=True, exist_ok=True)
    actions = read(md / "forum-actions.json", {})
    if f"buyer-report:{ledger}" not in actions and any(a.get("sku") == settled["sku"] and a.get("kind") in ("buyer_market_note", "buyer_report") and 0 <= now-a.get("queued_at", -1) < SKU_REPEAT for a in actions.values()):
        return "sku_recently_posted"
    text = (f"Buyer settlement report #{ledger}: {str(settled['sku'])[:80]}, paid {price} credits, "
            f"market value at settlement {value}, initial surplus {value-price}. "
            f"Recorded grade {settled.get('grade')}; warranty {settled.get('warranty_hours') or 0} h. "
            "Receipt and order matched. Later breakage/refunds are not assessed in this report.")
    text += comparison_text(md, settled, price, now)
    message = {"post": text, "refs": [ledger], "as": "buyer"}
    return enqueue(md, message, f"buyer-report:{ledger}", "buyer_report",
                   {"ledger": ledger, "sku": settled["sku"], "unit": settled.get("unit")}, now)
