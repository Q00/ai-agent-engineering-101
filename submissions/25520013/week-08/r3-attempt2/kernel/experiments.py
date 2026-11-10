"""Experiment ledger, before/after verdict and automatic revert for operator policy edits (human code, not editable by
the model).

DESIGN 14.4 step 1. Each policy edit the operator makes is registered as an experiment with its hypothesis, metric and
evaluation time in <root>/a2/experiments.json. When the time comes, tick() compares the metric over the window_h
hours after the edit with the window_h hours before it (shop side: the before value scaled by the market's revenue
change in the same scope, so a demand swing is not read as the edit's effect) and keeps the edit, reverts it with
policy_edit.kernel_set, or extends the window once. Plain code, no model calls. One running experiment per scope
(blocked()), because overlapping edits cannot be told apart in a before/after comparison.
"""

import json
import math
import os
import statistics as st
import tempfile
from collections import defaultdict

from kernel import brief
from kernel import common as c
from kernel import policy_edit as pe

LEDGER = os.path.join("a2", "experiments.json")
MIN_WINDOW_H, MAX_WINDOW_H = 2, 8
SLOW_KEYS = (
    "stock_target",
    "qty_per_buy",
)  # restock keys act through new stock, so they get a longer window
DEFAULT_WINDOW_H = {"shop": 3, "slow": 4, "buyer": 2}
MATURITY_H = {
    "shop": 2,
    "buyer": 0,
}  # breaks arrive a median 1.1 h after a sale; buyer deals are final at settle
JOIN_LOOKBACK_H = 12  # sales read before the before window, so a break joins the right sale of the same buyer and sku
METRIC = {"shop": "net_profit_per_h", "buyer": "saved_gain_per_h"}
SCORE_METRIC = "score_proxy_per_h"  # shop side with rev_weight > 0: net profit + rev_weight x revenue, per hour
STARS_MIN_N = 10  # reviews needed on both sides for the stars guard
STARS_DROP = 0.15
THIN_N = 5  # sales (deals) needed in the after window for a verdict
REL_MIN = 0.10  # an effect must also exceed this share of max(|before_adj|, FLOOR)
# FLOOR: judgment values, not measured: the smallest per-hour change worth acting on when the before value is near 0
FLOOR = {"shop": 100_000, "buyer": 0.05}
R_CLAMP = (0.5, 2.0)
RETRY_S = (
    600  # a failed revert or a failed evaluation is retried at most once per 10 min
)
STATUS_KO = {
    "running": "진행",
    "kept": "유지",
    "reverted": "되돌림",
    "voided": "무효(값이 바뀜)",
}
REASON_KO = {
    "stars": "별점 하락",
    "thin": "표본 적음",
    "clear": "잡음 넘음",
    "noise": "잡음 안",
    "point_estimate": "연장 후 점추정",
    "no_activity": "양쪽 거래 없음",
    "value_changed": "값이 따로 바뀜",
}
EXPECT_CHARS = 60


def ledger_path(root):
    return os.path.join(root, LEDGER)


def load(root):
    """The whole ledger list; [] for a missing or unreadable file."""
    rows = c.load_json(ledger_path(root), [])
    return rows if isinstance(rows, list) else []


def save(root, rows):
    p = ledger_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".experiments.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def scope_of(section, key=None):
    """-> (side, scope): "buyer:<kind>" for buyer.defaults.<kind>, "kind:<kind>" for defaults.<kind>, "shop:all" for
    shop, else the category leaf name (brief._leaf). key does not change the scope."""
    if section.startswith("buyer."):
        return "buyer", "buyer:" + section.rsplit(".", 1)[1]
    if section in pe.DEFAULTS:
        return "shop", "kind:" + section.split(".", 1)[1]
    if section == "shop":
        return "shop", "shop:all"
    return "shop", brief._leaf(section)


def _scope_kind(section, scope):
    """The kind a shop-side scope covers, None for shop:all."""
    if scope == "shop:all":
        return None
    return scope.split(":", 1)[1] if scope.startswith("kind:") else pe._kind(section)


def _overlap(sec_a, sec_b):
    (side_a, a), (side_b, b) = scope_of(sec_a), scope_of(sec_b)
    if side_a != side_b or a == b:
        return a == b
    if side_a == "buyer":
        return False
    if "shop:all" in (a, b):
        return True
    ka, kb = _scope_kind(sec_a, a), _scope_kind(sec_b, b)
    return (a.startswith("kind:") or b.startswith("kind:")) and ka == kb


def running(root):
    """The experiments with status running (non-dict ledger entries skipped)."""
    return [
        e for e in load(root) if isinstance(e, dict) and e.get("status") == "running"
    ]


def blocked(root, section, key, now):
    """None when an edit of (section, key) may start an experiment now, else a Korean message naming the running
    experiment whose scope overlaps and its evaluation time."""
    for e in running(root):
        try:
            if _overlap(section, e["section"]):
                return (
                    f"실험 #{e['id']} ({e['section']} {e['key']} {e['old']}→{e['new']}, 범위 {e['scope']})이 "
                    f"{c.kst(e['eval_ts'], '%H:%M')} 판정 전까지 진행 중이라 {section}은 지금 바꿀 수 없음"
                )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return None


def default_window_h(side, key):
    if side == "buyer":
        return DEFAULT_WINDOW_H["buyer"]
    return DEFAULT_WINDOW_H["slow" if key.split(".", 1)[0] in SLOW_KEYS else "shop"]


def _eval_ts(exp):
    return exp["started_ts"] + exp["window_h"] * 3600 + exp["maturity_h"] * 3600


def board_saved(root, now, login=None):
    """Our buyer's board "saved" in the latest snapshot at or before now (else the earliest one), None if absent."""
    login = c.owner_login(root) if login is None else login
    snaps = brief.board_snapshots(root)
    past = [s for s in snaps if s[0] <= now] or snaps[:1]
    if not past:
        return None
    b = c.load_json(past[-1][1], {}) or {}
    me = next(
        (
            r
            for r in b.get("buyers") or []
            if isinstance(r, dict) and c.is_mine(r, login)
        ),
        None,
    )
    v = (me or {}).get("saved")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def register(root, row, expect, window_h, now, traj, rev_weight=0.0):
    """Record a successful policy edit ({section, key, old, new}) as a running experiment. window_h None takes the
    default for the key; any value is clamped to [MIN_WINDOW_H, MAX_WINDOW_H] whole hours. rev_weight (shop side,
    the caller's board-derived worth of one won of revenue in net-profit won) turns the metric into
    score_proxy_per_h = net + rev_weight x revenue; it is kept for the whole experiment. -> the experiment."""
    rev_weight = float(rev_weight or 0.0)
    side, scope = scope_of(row["section"], row["key"])
    wh = default_window_h(side, row["key"]) if window_h is None else window_h
    wh = int(round(min(MAX_WINDOW_H, max(MIN_WINDOW_H, float(wh)))))
    rows = load(root)
    exp = {
        "id": 1
        + max(
            [
                e["id"]
                for e in rows
                if isinstance(e, dict) and isinstance(e.get("id"), int)
            ]
            or [0]
        ),
        "section": row["section"],
        "key": row["key"],
        "old": row.get("old"),
        "new": row.get("new"),
        "started_ts": now,
        "window_h": wh,
        "maturity_h": MATURITY_H[side],
        "scope": scope,
        "side": side,
        "metric": SCORE_METRIC if side == "shop" and rev_weight > 0 else METRIC[side],
        "rev_weight": rev_weight if side == "shop" else 0.0,
        "expect": str(expect or "")[:500],
        "status": "running",
        "extended": False,
    }
    if side == "buyer":
        exp["s0"] = board_saved(root, now)
    exp["eval_ts"] = _eval_ts(exp)
    rows.append(exp)
    save(root, rows)
    traj.append(
        "operator",
        "experiment_start",
        **{
            k: exp[k]
            for k in (
                "id",
                "section",
                "key",
                "old",
                "new",
                "scope",
                "side",
                "metric",
                "rev_weight",
                "window_h",
                "eval_ts",
                "expect",
            )
        },
    )
    return exp


def _kind_of(item, d, category):
    k = (item or {}).get("kind") or d.get("kind")
    return k if k in ("food", "goods") else pe._kind(category or "")


def _buyer_deal(e, d, cat):
    """One of our buyer's purchases from a market settle row, or None. Shape confirmed on live data (11-10, data keys
    amount, buyer, category, grade, kind, list_price (sometimes missing), order, shop, sku, warranty_hours), as attempt 1
    ledger.py _market_entry/norm_settle writes it: type "settle", src "market", role "market" (not "buyer"), data.own_owner True and our
    login in data.buyer (common.settle_side), data.amount = price paid, data.list_price when the market sends it, else
    the catalog list price of data.sku. A deal with no list price cannot be valued and is skipped."""
    lp = d.get("list_price")
    lp = (
        lp
        if isinstance(lp, (int, float)) and not isinstance(lp, bool) and lp > 0
        else brief._list_price(cat, d.get("sku"))
    )
    if not lp or not isinstance(d.get("amount"), (int, float)):
        return None
    item = cat.get(d.get("sku")) or {}
    return {
        "ts": e["ts"],
        "amount": float(d["amount"]),
        "lp": float(lp),
        "kind": _kind_of(item, d, item.get("category") or d.get("category")),
    }


def read_events(root, lo, hi, login=None, cat=None):
    """Rows with ts in [lo, hi) from .r3/events.jsonl, joined as brief.net_profit_data joins them, but for any window:
    -> {"sales": [our shop sales with cost, leaf, kind, order, warranty hours, broke_at], "market": [(ts, amount, leaf,
    kind) of every market settle], "deals": [our buyer's purchases], "reviews": {order: stars}}."""
    login = c.owner_login(root) if login is None else login
    cat = brief._catalog(root) if cat is None else cat
    path = os.path.join(root, ".r3/events.jsonl")
    nb = int(brief.NET_BYTES_PER_H * (max(0.0, hi - lo) / 3600 + 2))
    inside = lambda e: isinstance(e.get("ts"), (int, float)) and lo <= e["ts"] < hi
    cost = {}
    for needle, mine in (
        (b'"restock"', True),
        (b'"unit_gone"', False),
    ):  # unit_gone wins, as in net_profit_data
        for e in c.tail_json(path, nb, needle):
            d = e.get("data") or {}
            if (
                e.get("type") == needle.strip(b'"').decode()
                and (e.get("mine") or not mine)
                and d.get("unit")
                and isinstance(d.get("cost"), (int, float))
            ):
                cost[d["unit"]] = (float(d["cost"]), d.get("category"))
    sales, market, deals, by_key = [], [], [], defaultdict(list)
    for e in c.tail_json(path, nb, b'"settle"'):
        d = e.get("data") or {}
        if e.get("type") != "settle" or e.get("src") != "market" or not inside(e):
            continue
        item = cat.get(d.get("sku")) or {}
        u = cost.get(d.get("unit"))
        path_ = item.get("category") or d.get("category") or (u[1] if u else None)
        leaf, kind, a = (
            brief._leaf(path_),
            _kind_of(item, d, path_),
            float(d.get("amount") or 0),
        )
        market.append((e["ts"], a, leaf, kind))
        side = c.settle_side(d, login)
        if side == "buyer":
            x = _buyer_deal(e, d, cat)
            if x:
                deals.append(x)
        elif side == "shop":
            s = {
                "ts": e["ts"],
                "amount": a,
                "cost": u[0] if u else None,
                "leaf": leaf,
                "kind": kind,
                "order": d.get("order"),
                "wh": d.get("warranty_hours") or 0,
                "broke_at": None,
            }
            sales.append(s)
            by_key[(json.dumps(d.get("buyer")), d.get("sku"))].append(s)
    breaks = []
    for e in c.tail_json(path, nb, b'"broken"'):
        d = e.get("data") or {}
        if e.get("type") == "broken" and e.get("src") == "market" and e.get("mine"):
            at = (
                d.get("broke_at")
                if isinstance(d.get("broke_at"), (int, float))
                else e.get("ts")
            )
            if isinstance(at, (int, float)) and lo <= at < hi:
                breaks.append((at, json.dumps(d.get("buyer")), d.get("sku")))
    for at, buyer, sku in sorted(breaks):
        s = max(
            (
                x
                for x in by_key.get((buyer, sku), ())
                if x["ts"] <= at and x["broke_at"] is None
            ),
            key=lambda x: x["ts"],
            default=None,
        )
        if s is not None:
            s["broke_at"] = at
    reviews = {}
    for e in c.tail_json(path, nb, b'"review"'):
        d = e.get("data") or {}
        if (
            e.get("type") == "review"
            and e.get("role") == "shop"
            and inside(e)
            and d.get("order")
            and isinstance(d.get("stars"), (int, float))
            and not isinstance(d.get("stars"), bool)
        ):
            reviews[d["order"]] = d["stars"]
    return {"sales": sales, "market": market, "deals": deals, "reviews": reviews}


def _in_scope(scope, leaf, kind):
    if scope == "shop:all":
        return True
    if scope.startswith(("kind:", "buyer:")):
        return kind == scope.split(":", 1)[1]
    return leaf == scope


def _buckets(t0, t1):
    n = max(1, int(round((t1 - t0) / 3600)))
    return n, (lambda ts: min(n - 1, int((ts - t0) // 3600)))


def refund(s):
    """The refund a sale counts in the metric: its amount when it broke within MATURITY_H["shop"] hours of the sale
    and within its warranty (+brief.NET_WARRANTY_SLACK_S, the net_profit_data rule), else 0."""
    if s["broke_at"] is None or not s["wh"]:
        return 0.0
    dt = s["broke_at"] - s["ts"]
    return (
        s["amount"]
        if dt <= MATURITY_H["shop"] * 3600
        and dt <= s["wh"] * 3600 + brief.NET_WARRANTY_SLACK_S
        else 0.0
    )


def shop_metric(data, scope, t0, t1, rev_weight=0.0):
    """Over our sales settled in [t0, t1) within scope: net = gross (amount - unit cost, 0 without a known cost) minus
    matured refunds, revenue = sum of amounts, both per hour; value = net + rev_weight x revenue (net alone when 0).
    With n, market revenue in scope, review stars and hourly buckets of value."""
    n_b, idx = _buckets(t0, t1)
    hours, buckets, n, stars = (t1 - t0) / 3600, [0.0] * n_b, 0, []
    net = rev = 0.0
    for s in data["sales"]:
        if t0 <= s["ts"] < t1 and _in_scope(scope, s["leaf"], s["kind"]):
            n += 1
            x = (s["amount"] - s["cost"] if s["cost"] is not None else 0.0) - refund(s)
            net, rev = net + x, rev + s["amount"]
            buckets[idx(s["ts"])] += x + rev_weight * s["amount"]
            if s["order"] in data["reviews"]:
                stars.append(data["reviews"][s["order"]])
    mkt = sum(
        a
        for ts, a, leaf, kind in data["market"]
        if t0 <= ts < t1 and _in_scope(scope, leaf, kind)
    )
    return {
        "value": sum(buckets) / hours,
        "net": net / hours,
        "revenue": rev / hours,
        "n": n,
        "market": mkt,
        "stars": st.mean(stars) if stars else None,
        "n_reviews": len(stars),
        "buckets": buckets,
    }


def buyer_metric(data, scope, t0, t1, s0):
    """saved_gain_per_h over our buyer's purchases settled in [t0, t1) of the scope's kind: sum of (surplus_frac - s0)
    per hour, surplus_frac = (list - paid) / list. The board's saved is sum(surplus_frac) / (deals + 100), so a deal
    raises it only when its surplus_frac is above the current value s0."""
    n_b, idx = _buckets(t0, t1)
    hours, buckets, n, s0 = (t1 - t0) / 3600, [0.0] * n_b, 0, s0 or 0.0
    for x in data["deals"]:
        if t0 <= x["ts"] < t1 and _in_scope(scope, None, x["kind"]):
            n += 1
            buckets[idx(x["ts"])] += (x["lp"] - x["amount"]) / x["lp"] - s0
    return {"value": sum(buckets) / hours, "n": n, "buckets": buckets}


def evaluate(root, exp, now, data=None):
    """Verdict for an experiment as of now (no writes): the window_h hours after started_ts against the window_h hours
    before it. -> dict with every number used and decision better | worse | thin | unclear and its reason."""
    side, scope, wh, t = exp["side"], exp["scope"], exp["window_h"], exp["started_ts"]
    w = wh * 3600
    if data is None:
        data = read_events(root, t - w - JOIN_LOOKBACK_H * 3600, now)
    if side == "buyer":
        b, a = (
            buyer_metric(data, scope, t - w, t, exp.get("s0")),
            buyer_metric(data, scope, t, t + w, exp.get("s0")),
        )
        r = 1.0
    else:
        rw = exp.get("rev_weight") or 0.0
        b, a = shop_metric(data, scope, t - w, t, rw), shop_metric(data, scope, t, t + w, rw)
        r = a["market"] / b["market"] if a["market"] and b["market"] else 1.0
        r = min(R_CLAMP[1], max(R_CLAMP[0], r))
    before_adj = b["value"] * r
    effect = a["value"] - before_adj
    ab, bb = a["buckets"], [x * r for x in b["buckets"]]
    se = (
        math.sqrt(st.variance(ab) / len(ab) + st.variance(bb) / len(bb))
        if len(ab) >= 2 and len(bb) >= 2
        else None
    )
    v = {
        "metric": exp["metric"],
        "window_h": wh,
        "before": b["value"],
        "before_adj": before_adj,
        "after": a["value"],
        "r": r,
        "effect": effect,
        "se": se,
        "n_before": b["n"],
        "n_after": a["n"],
    }
    if side == "buyer":
        v["s0"] = exp.get("s0")
    else:
        v.update(
            rev_weight=exp.get("rev_weight") or 0.0,
            net_before=b["net"],
            net_after=a["net"],
            revenue_before=b["revenue"],
            revenue_after=a["revenue"],
            market_before=b["market"],
            market_after=a["market"],
            stars_before=b["stars"],
            stars_after=a["stars"],
            n_reviews_before=b["n_reviews"],
            n_reviews_after=a["n_reviews"],
        )
    if (
        side == "shop"
        and b["n_reviews"] >= STARS_MIN_N
        and a["n_reviews"] >= STARS_MIN_N
        and a["stars"] < b["stars"] - STARS_DROP
    ):
        v.update(decision="worse", reason="stars")
    elif a["n"] < THIN_N:
        v.update(decision="thin", reason="thin")
    elif (
        se is not None
        and abs(effect) > se
        and abs(effect) > REL_MIN * max(abs(before_adj), FLOOR[side])
    ):
        v.update(decision="better" if effect > 0 else "worse", reason="clear")
    else:
        v.update(decision="unclear", reason="noise")
    return v


def _same(a, b):
    if (
        isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        return abs(a - b) < 1e-9
    return a == b


def _end(exp, status, v, now, traj):
    exp.update(status=status, verdict=v, ended_ts=now)
    exp.pop("last_error", None)
    traj.append(
        "operator",
        "experiment_end",
        id=exp["id"],
        section=exp["section"],
        key=exp["key"],
        old=exp["old"],
        new=exp["new"],
        status=status,
        verdict=v,
    )


def _step(root, exp, now, traj, categories, validate):
    """Decide one due experiment. -> True when it ended or was extended."""
    pol = c.load_json(pe.path(root), None)
    if not isinstance(pol, dict):
        raise ValueError("policy.json unreadable")
    cur = pe.effective(pol, exp["section"], exp["key"])
    if not _same(cur, exp["new"]):
        _end(
            exp,
            "voided",
            {"decision": "voided", "reason": "value_changed", "value_now": cur},
            now,
            traj,
        )
        return True
    v = evaluate(root, exp, now)
    if v["decision"] in ("better", "worse"):
        v["final"] = "decision"
    elif not exp.get("extended"):
        exp.update(
            window_h=min(MAX_WINDOW_H, exp["window_h"] * 2),
            extended=True,
            first_verdict=v,
        )
        exp["eval_ts"] = _eval_ts(exp)
        exp.pop("last_error", None)
        traj.append(
            "operator",
            "experiment_extend",
            id=exp["id"],
            section=exp["section"],
            key=exp["key"],
            window_h=exp["window_h"],
            eval_ts=exp["eval_ts"],
            verdict=v,
        )
        return True
    elif v["n_before"] == 0 and v["n_after"] == 0:
        v.update(final="no_activity")
    else:
        v.update(final="point_estimate")
    keep = v["decision"] == "better" or (
        v["decision"] != "worse" and (v["final"] == "no_activity" or v["effect"] >= 0)
    )
    if keep:
        _end(exp, "kept", v, now, traj)
        return True
    res = pe.kernel_set(
        root,
        exp["section"],
        exp["key"],
        exp["old"],
        traj,
        now,
        f"실험 #{exp['id']} 판정 {v['decision']} ({v['reason']}): {exp['old']}로 되돌림",
        categories,
        validate,
    )
    if not res.get("ok"):
        exp.update(last_error=f"revert failed: {res.get('error')}", last_try_ts=now)
        return False
    _end(exp, "reverted", v, now, traj)
    return True


def tick(root, now, traj, categories=(), validate=None):
    """Decide every running experiment whose eval_ts has come. Never raises: a failure is kept as last_error on the
    experiment and retried after RETRY_S. -> the experiments that ended or were extended."""
    try:
        rows = load(root)
    except Exception:  # noqa: BLE001 - an unreadable ledger must not stop the operator wake
        return []
    out, dirty = [], False
    for exp in rows:
        try:
            if (
                not isinstance(exp, dict)
                or exp.get("status") != "running"
                or now < exp["eval_ts"]
            ):
                continue
            if (
                exp.get("last_try_ts") is not None
                and now - exp["last_try_ts"] < RETRY_S
            ):
                continue
            dirty = True
            if _step(root, exp, now, traj, categories, validate):
                out.append(exp)
        except Exception as e:  # noqa: BLE001 - one bad entry must not stop the others
            if isinstance(exp, dict):
                exp.update(last_error=f"{type(e).__name__}: {e}"[:300], last_try_ts=now)
                dirty = True
    if dirty:
        try:
            save(root, rows)
        except Exception:  # noqa: BLE001
            pass
    return out


def _num(v, side, rate=True):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "-"
    if side != "shop":
        return f"{v:.3f}" + ("/h" if rate else "")
    return (c.fmt(v) if abs(v) >= 1e5 else f"{v:,.0f}") + ("/h" if rate else "")  # c.fmt prints 1000 as 1e+03


def lines(root, now, last=6):
    """[실험] briefing lines: counts, each running experiment, the last `last` ended ones with their numbers."""
    rows = [e for e in load(root) if isinstance(e, dict) and "id" in e]
    run = [e for e in rows if e.get("status") == "running"]
    ended = sorted(
        (e for e in rows if e.get("status") in ("kept", "reverted", "voided")),
        key=lambda e: e.get("ended_ts") or 0,
    )[-last:]
    n = lambda s: sum(1 for e in ended if e["status"] == s)
    out = [
        f"[실험] 진행 {len(run)}건, 최근 판정 {len(ended)}건 중 유지 {n('kept')}, 되돌림 {n('reverted')}"
    ]
    for e in run:
        exp = str(e.get("expect") or "")
        exp = exp[:EXPECT_CHARS] + ("…" if len(exp) > EXPECT_CHARS else "")
        out.append(
            f"  진행 #{e['id']} {brief._leaf(e.get('section'))} {e.get('key')} {e.get('old')}→{e.get('new')}, "
            f"판정 {c.kst(e['eval_ts'], '%H:%M') if isinstance(e.get('eval_ts'), (int, float)) else '-'}"
            + (" (연장)" if e.get("extended") else "")
            + (f", 오류 {e['last_error'][:60]}" if e.get("last_error") else "")
            + f", 가설: {exp}"
        )
    for e in reversed(ended):
        v, side = e.get("verdict") or {}, e.get("side")
        head = f"  #{e['id']} {e.get('key')} {e.get('old')}→{e.get('new')} {STATUS_KO.get(e['status'], e['status'])}"
        if e["status"] == "voided":
            out.append(head)
            continue
        reason = REASON_KO.get(
            v.get("final")
            if v.get("final") not in (None, "decision")
            else v.get("reason"),
            "",
        )
        out.append(
            head
            + f": 효과 {_num(v.get('effect'), side)} (전 보정 {_num(v.get('before_adj'), side)}, "
            f"후 {_num(v.get('after'), side)}"
            + (f", se {_num(v['se'], side, False)}" if v.get("se") is not None else "")
            + f", {'거래' if side == 'buyer' else '판매'} {v.get('n_before', '-')}→{v.get('n_after', '-')})"
            + (
                f", 순이익 {_num(v.get('net_before'), side)}→{_num(v.get('net_after'), side)}"
                f", 매출 {_num(v.get('revenue_before'), side)}→{_num(v.get('revenue_after'), side)}"
                if side == "shop"
                else ""
            )
            + f", {reason}"
        )
    return out


def summary(root, now, hours=24):
    """Counts by status of experiments ended in the last `hours` (running: all now running) and the hit rate
    kept / (kept + reverted), None when neither happened."""
    rows = [e for e in load(root) if isinstance(e, dict)]
    since = now - hours * 3600
    out = {s: 0 for s in STATUS_KO}
    for e in rows:
        s = e.get("status")
        if s == "running" or (s in out and (e.get("ended_ts") or 0) >= since):
            out[s] += 1
    kr = out["kept"] + out["reverted"]
    return {"hours": hours, "counts": out, "hit_rate": out["kept"] / kr if kr else None}
