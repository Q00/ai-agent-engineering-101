"""Buyer forum: a purchase record that says where its price sits in the market (W-20261109-2055 GPT Pro buyer candidate 1).

Our buyer posts drew 1.03 likes a post in the 24 h to 11/09 21:00 (54% of them none) against 1.5-1.75 for the top
authors; usagi's records name the market's price beside their own. Arm B adds to a [구매 기록] "시장 중앙값 7,000원(정가의
70%, 같은 등급·보증 6h 이상 최근 3시간 n건)": the median of amount / list over the market's settles of the last
TREND_HOURS for the same SKU x grade x warranty (6 h or more, or less or none, as buyer_post.trend groups and words it),
every other deal (this one's ledger seq left out, ours counted like anyone's), no false grade claim, finite amounts, at
least MIN_N of them. Arm A posts the record as it is. Trend posts are untouched.

Every record posted while the study is on is measured in .market/measure/buyer_post_market.jsonl (ts, study, ledger,
arm, eligible: the note could be written, applied: arm B and eligible, n, median_share), so the verdict joins forum
posts by refs[0] == ledger and compares likes per post of eligible records by arm. The outbox line is unchanged.

Switch: .market/buyer_post_market.json {"enabled": true, "study": "<id>", "salt": "<salt>", "start": <epoch>,
"end": <epoch>}: on only from start until end (the study's ON time and its registry maximum), so arm B stops at end
even if nobody turns the switch off. Missing, unreadable, without the window or anything else is off, and off is the
old post exactly. BUYER_POST_MARKET=0 in the environment is off too. Read at every post (review.py runs per paid deal),
so no restart either way. Arms: sha256(salt:ledger seq), first byte odd is B.
"""
import hashlib
import json
import math
import os
import statistics
import time

import buyer_post

SWITCH = "buyer_post_market.json"
MEASURE = "buyer_post_market.jsonl"
MIN_N = 5


def _epoch(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def config(md, env=None, now=None):
    """{"study", "salt", "start", "end"} with the switch on and now in [start, end), else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_POST_MARKET") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt, start, end = d.get("study"), d.get("salt"), _epoch(d.get("start")), _epoch(d.get("end"))
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    now = time.time() if now is None else now
    if start is None or end is None or not start <= now < end:
        return None
    return {"study": study, "salt": salt, "start": start, "end": end}


def arm(cfg, ledger):
    if not cfg or not isinstance(ledger, int) or isinstance(ledger, bool):
        return None
    return "B" if hashlib.sha256(f"{cfg['salt']}:{ledger}".encode("utf-8", "surrogatepass")).digest()[0] % 2 else "A"


def median_share(md, d, now):
    """(n, median amount / list) of the other settles like this deal in the last TREND_HOURS; share None if n < MIN_N."""
    value = d.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return 0, None
    wh = d.get("warranty_hours")
    key = (d.get("sku"), d.get("grade") or "", isinstance(wh, (int, float)) and wh >= 6)
    shares = [r["amount"] / value for r in buyer_post.recent_settles(md, now - buyer_post.TREND_HOURS * 3600)
              if r["ts"] <= now and r["seq"] != d.get("ledger") and r.get("honest") is not False and math.isfinite(r["amount"])
              and (r.get("sku"), r.get("grade") or "", buyer_post._warranted(r)) == key]
    if len(shares) < MIN_N:
        return len(shares), None
    med = statistics.median(shares)
    return (len(shares), round(med, 4)) if math.isfinite(med) else (len(shares), None)


def note(d, n, share):
    terms = ["같은 등급"] if d.get("grade") else []
    if d.get("kind") != "food":
        wh = d.get("warranty_hours")
        terms.append("보증 6h 이상" if isinstance(wh, (int, float)) and wh >= 6 else "보증 6h 미만·없음")
    scope = " ".join(["·".join(terms)] * bool(terms) + [f"최근 {buyer_post.TREND_HOURS}시간 {n}건"])
    return f"시장 중앙값 {round(share * d['value']):,}원(정가의 {round(100 * share)}%, {scope})"


def decorate(md, d, post, now, env=None):
    """(post, measure row or None) for a purchase record. Never raises: any failure is the post as it was."""
    try:
        cfg = config(md, env, now)
        a = arm(cfg, d.get("ledger"))
        if a is None:
            return post, None
        try:
            n, share = median_share(md, d, now)
        except Exception:  # noqa: BLE001 - no note, the record is still measured as not eligible
            n, share = 0, None
        try:
            words = note(d, n, share) if share is not None else None
        except Exception:  # noqa: BLE001 - measured as not eligible in both arms
            words = None
        eligible = words is not None
        tail = f" | ledger #{d['ledger']}"
        applied = a == "B" and eligible and post.endswith(tail)
        if applied:
            post = post[:-len(tail)] + " | " + words + tail
        return post, {"ts": now, "study": cfg["study"], "v": 1, "ledger": d["ledger"], "sku": d.get("sku"), "arm": a,
                      "eligible": eligible, "applied": applied, "n": n, "median_share": share}
    except Exception:  # noqa: BLE001
        return post, None


def record(md, row):
    """Appends the measure row; never raises (a post already in the outbox is not taken back)."""
    if not row:
        return
    try:
        os.makedirs(os.path.join(md, "measure"), exist_ok=True)
        with open(os.path.join(md, "measure", MEASURE), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
