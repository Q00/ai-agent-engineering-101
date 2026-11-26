"""Week 08: where we stand in the Players race (student table) and where the board's paces take it by the end.

Owner, 11-13: the goal is first place among students (student_total, read here as points of 200). The buyer's
Saved is the lever that moves most: a student buyer passed in Saved is +1.1 for us, and -1.1 for that player when
it is the leader. Both managers get the same picture: buyer_manager the buyer part and alerts 1-4, manager (shop)
the Book part and alert 5. Everything is computed from the board snapshots in data/market.db (no model, no state
but the alert file), so an hour can be recomputed later as it was.

Projection: every buyer keeps its pace of the last PACE_H hours on the board (deals per hour and Saved per deal,
from the change in saved x (deals + 100)), shops keep their board numbers. Saved per deal is what decides the
order in the end, not today's rise: a buyer converges to its own per-deal mean.

Alerts (raised once when they turn on, again only after they cleared; the active ones are always in the block):
  1 per_deal  our Saved per deal over the last 3 h under PER_DEAL_MIN (with 50+ deals)
  2 flip:<x>  a student buyer we passed by the end 6 h ago (by FLIP_BAND+) we no longer pass (by FLIP_BAND+)
  3 gap       the projected end gap to the leader worse by more than GAP_WORSE points than 6 h ago
  4 share     our share of all buyers' deals over the last 3 h fell more than SHARE_DROP under the 12 h before
  5 book      the Book gap to the place above (or the lead over the place below at 2nd) not better than 6 h ago
"""
import calendar
import json
import sqlite3
import time

import model
import policy

US = model.SHOP_NAME                      # buyer, shop and player are all "inextro"
END = calendar.timegm((2026, 11, 17, 14, 59, 0))     # 11-17 23:59 KST, the round's end
PACE_H, SHORT_H, BACK_H = 12, 3, 6
PER_DEAL_MIN, GAP_WORSE, SHARE_DROP = 0.85, 1.0, 0.20
FLIP_BAND = 0.002                         # a pass counts as won or lost only beyond this end margin in Saved
STATE = {"buyer": policy.DATA / "race_alerts_buyer.json", "shop": policy.DATA / "race_alerts_shop.json"}
DB = policy.DATA / "market.db"
KST = 9 * 3600


def board_at(db, t):
    """The newest whole snapshot at or before t. The board sometimes shows our shop for one snapshot with revenue 0
    and stars_rank 3.0 (11-13 13:29 KST: student_total 1.212 against 1.66 before and after); such a snapshot is
    passed over for the one before it."""
    for ts, raw in db.execute("SELECT ts, raw FROM board WHERE ts <= ? ORDER BY ts DESC LIMIT 6", (t,)):
        bd = json.loads(raw)
        shop = next((x for x in bd["shops"] if x["name"] == US), {})
        buyer = next((x for x in bd["buyers"] if x["name"] == US), {})
        if shop.get("revenue") and buyer.get("saved") is not None:
            return ts, bd
    return None, None


def pct(vals, v):
    """The board's percentile: the others under v, a tie counted half (checked 11-13 on student_shop: stars_rank
    has many ties, and counting them as nothing put other shops up to 0.011 low)."""
    if len(vals) < 2:
        return 0.0
    return (sum(o < v for o in vals) + 0.5 * (sum(o == v for o in vals) - 1)) / (len(vals) - 1)


def students(bd):
    """Student buyers and shops with a score, and the student players with their buyer and shop names."""
    B = [x for x in bd["buyers"] if x.get("all") is not None]
    S = [x for x in bd["shops"] if x.get("all") is not None]
    P = [p for p in bd["players"] if p.get("student")]
    bn = {(p.get("buyer") or {}).get("name") for p in P}
    sn = {(p.get("shop") or {}).get("name") for p in P}
    return [x for x in B if x["name"] in bn], [x for x in S if x["name"] in sn], P


def scores(bd, buyers=None):
    """student_total per player, with the buyers' saved/deals/posts/forum replaced by `buyers` when given."""
    sb, ss, P = students(bd)
    cur = {x["name"]: {k: x.get(k) or 0 for k in ("saved", "deals", "posts", "forum")} for x in sb}
    if buyers:
        cur.update({k: v for k, v in buyers.items() if k in cur})
    def ball(n):
        return sum(w * pct([cur[y][k] for y in cur], cur[n][k]) for k, w in (("saved", .5), ("deals", .1), ("posts", .1))) + .3 * cur[n]["forum"]
    g = lambda y, k: y.get(k) or 0
    shop = {x["name"]: .5 * pct([g(y, "book") for y in ss], g(x, "book")) + .3 * pct([g(y, "revenue") for y in ss], g(x, "revenue"))
            + .2 * pct([g(y, "stars_rank") for y in ss], g(x, "stars_rank")) for x in ss}
    out = {}
    for p in P:
        b, s = (p.get("buyer") or {}).get("name"), (p.get("shop") or {}).get("name")
        out[p["player"]] = (ball(b) if b in cur else 0) + shop.get(s, 0)
    return out, cur


def paces(db, t, hours):
    """Per buyer over the `hours` before t: (Saved per deal, deals per hour, posts per hour), from board deltas."""
    t1, b1 = board_at(db, t)
    t0, b0 = board_at(db, t - hours * 3600)
    if not b0:
        return {}, 0
    old = {x["name"]: x for x in b0["buyers"]}
    h = (t1 - t0) / 3600 or 1
    out = {}
    for x in b1["buyers"]:
        o = old.get(x["name"])
        if not o or x.get("saved") is None or o.get("saved") is None:
            continue
        dk = (x.get("deals") or 0) - (o.get("deals") or 0)
        s = x["saved"] * ((x.get("deals") or 0) + 100) - o["saved"] * ((o.get("deals") or 0) + 100)
        out[x["name"]] = (s / dk if dk > 0 else None, dk / h, ((x.get("posts") or 0) - (o.get("posts") or 0)) / h)
    return out, h


def ahead(x, pc, hours):
    """A buyer's saved/deals/posts after `hours` more at pace pc."""
    n, sv = x.get("deals") or 0, x.get("saved") or 0
    m, rate, prate = pc if pc else (None, 0, 0)
    k = rate * hours if m is not None else 0
    return {"saved": (sv * (n + 100) + (m or 0) * k) / (n + 100 + k), "deals": n + k,
            "posts": (x.get("posts") or 0) + max(prate, 0) * hours, "forum": x.get("forum") or 0}


def race(db, t):
    """The race as it stood at t (KST stamps), projected to END at the board paces of the PACE_H hours before t."""
    ts, bd = board_at(db, t)
    sb, ss, P = students(bd)
    pc, _ = paces(db, ts, PACE_H)
    short, _ = paces(db, ts, SHORT_H)
    left = max(0.0, (END - ts) / 3600)
    now, _ = scores(bd)
    proj_b = {x["name"]: ahead(x, pc.get(x["name"]), left) for x in sb}
    end, _ = scores(bd, proj_b)
    def table(sc):
        o = sorted(sc.items(), key=lambda kv: -kv[1])
        rank = next(i for i, (n, _) in enumerate(o) if n == US) + 1
        lead = o[0] if o[0][0] != US else o[1]
        return {"rank": rank, "ours": round(sc[US] * 100, 1), "first_other": lead[0], "first_other_points": round(lead[1] * 100, 1),
                "gap_points": round((sc[US] - lead[1]) * 100, 1)}
    me = next(x for x in sb if x["name"] == US)
    mine_end = proj_b[US]["saved"]
    margins = {x["name"]: round(mine_end - proj_b[x["name"]]["saved"], 4) for x in sb
               if x["name"] != US and (x.get("saved") or 0) > me["saved"]}
    rivals = []
    for x in sorted(sb, key=lambda y: -(y.get("saved") or 0)):
        if x["name"] == US or (x.get("saved") or 0) <= me["saved"]:
            continue
        p = pc.get(x["name"]); e = proj_b[x["name"]]["saved"]
        if mine_end - e < -0.01:
            continue
        cross = None
        for h in range(0, int(left) + 1):
            if ahead(me, pc.get(US), h)["saved"] > ahead(x, p, h)["saved"]:
                cross = h
                break
        rivals.append({"buyer": x["name"], "saved_now": round(x["saved"], 4), "per_deal": round(p[0], 3) if p and p[0] else None,
                       "deals_h": round(p[1], 1) if p else 0, "gap_now": round(me["saved"] - x["saved"], 4),
                       "gap_end": round(mine_end - e, 4), "we_pass_in_h": cross})
    tot = sum(v[1] for v in short.values() if v[1] > 0)
    shops = sorted(ss, key=lambda x: -(x.get("book") or 0))
    bi = next(i for i, x in enumerate(shops) if x["name"] == US)
    book = {"rank": bi + 1, "ours_M": round((shops[bi].get("book") or 0) / 1e6, 1),
            "above": {"shop": shops[bi - 1]["name"], "M": round((shops[bi - 1].get("book") or 0) / 1e6, 1)} if bi else None,
            "below": {"shop": shops[bi + 1]["name"], "M": round((shops[bi + 1].get("book") or 0) / 1e6, 1)} if bi + 1 < len(shops) else None}
    return {"at_kst": time.strftime("%m-%d %H:%M", time.gmtime(ts + KST)), "hours_left": round(left, 1),
            "now": table(now), "end_at_board_paces": table(end),
            "buyer": {"saved": round(me["saved"], 4), "saved_rank_students": 1 + sum((x.get("saved") or 0) > me["saved"] for x in sb),
                      "students": len(sb), "saved_end": round(mine_end, 4),
                      "per_deal_3h": round(short[US][0], 3) if short.get(US) and short[US][0] else None,
                      "per_deal_12h": round(pc[US][0], 3) if pc.get(US) and pc[US][0] else None,
                      "deals_h_3h": round(short[US][1], 1) if short.get(US) else 0, "deals_3h": round(short[US][1] * SHORT_H) if short.get(US) else 0,
                      "market_share_3h": round(short[US][1] / tot, 4) if tot and short.get(US) else None},
            "rivals_in_reach": rivals, "book": book, "_end_margins": margins}


def share_between(db, t0, t1):
    a, b = board_at(db, t0)[1], board_at(db, t1)[1]
    old = {x["name"]: x.get("deals") or 0 for x in a["buyers"]}
    d = {x["name"]: (x.get("deals") or 0) - old.get(x["name"], 0) for x in b["buyers"]}
    tot = sum(v for v in d.values() if v > 0)
    return d.get(US, 0) / tot if tot else None


def checks(db, now, role):
    """(alerts on now, the race now): role "buyer" checks 1-4, "shop" check 5."""
    r, r0 = race(db, now), race(db, now - BACK_H * 3600)
    on = {}
    # a rescore of past deals (patch 3.6, 11-12 17:39) moves Saved without deals: a Saved per deal outside -1..1 in
    # either race means a pace window holds one, and every pace-based alert would be noise until it has passed
    pd = [x["buyer"][k] for x in (r, r0) for k in ("per_deal_3h", "per_deal_12h") if x["buyer"][k] is not None]
    if any(abs(v) > 1 for v in pd):
        r["rescored_in_window"] = True
        return on, r
    if role == "buyer":
        b = r["buyer"]
        if b["per_deal_3h"] is not None and b["deals_3h"] >= 50 and b["per_deal_3h"] < PER_DEAL_MIN:
            on["per_deal"] = (f"건당 Saved가 최근 3시간 {b['per_deal_3h']:.3f}로 {PER_DEAL_MIN} 아래입니다 (12시간 {b['per_deal_12h']}). "
                              f"추월 대상(yms979·Kiro729 등)과의 마감 격차가 얇아 이 값이 이어지면 추월이 흔들립니다.")
        old, new = r0["_end_margins"], r["_end_margins"]    # margins within FLIP_BAND of 0 are a coin toss
        for n in sorted(old):
            if old[n] >= FLIP_BAND and new.get(n, 1) <= -FLIP_BAND:
                on[f"flip:{n}"] = (f"{BACK_H}시간 전 투영에서는 마감까지 Saved로 {n}을 넘었는데(격차 {old[n]:+.4f}) "
                                   f"지금 투영에서는 못 넘습니다(격차 {new[n]:+.4f}).")
        d = r["end_at_board_paces"]["gap_points"] - r0["end_at_board_paces"]["gap_points"]
        if d < -GAP_WORSE:
            on["gap"] = (f"마감 투영 1위와의 차이가 {BACK_H}시간 전 {r0['end_at_board_paces']['gap_points']:+.1f}점에서 "
                         f"{r['end_at_board_paces']['gap_points']:+.1f}점으로 {-d:.1f}점 나빠졌습니다 (1위 {r['end_at_board_paces']['first_other']}).")
        s3, s12 = share_between(db, now - SHORT_H * 3600, now), share_between(db, now - (SHORT_H + 12) * 3600, now - SHORT_H * 3600)
        if s3 is not None and s12 and s3 < s12 * (1 - SHARE_DROP):
            on["share"] = f"시장 전체 buyer 거래 중 우리 몫이 최근 3시간 {s3:.2%}로 그 전 12시간 {s12:.2%}보다 {1 - s3 / s12:.0%} 줄었습니다."
    else:
        bk, bk0 = r["book"], r0["book"]
        if bk["rank"] > 2 and bk["above"] and bk0["above"]:
            g, g0 = bk["above"]["M"] - bk["ours_M"], bk0["above"]["M"] - bk0["ours_M"]
            if g >= g0:
                on["book"] = (f"Book {bk['rank']}위: 위 가게 {bk['above']['shop']}와의 격차가 {BACK_H}시간 전 {g0:.1f}M에서 {g:.1f}M로 "
                              f"줄지 않았습니다. Book 한 계단은 학생 점수 +1.1점(200점 기준)입니다.")
        elif bk["rank"] == 2 and bk["below"] and bk0["below"]:
            g, g0 = bk["ours_M"] - bk["below"]["M"], bk0["ours_M"] - bk0["below"]["M"]
            if g < g0:
                on["book"] = f"Book 2위: 3위 {bk['below']['shop']}와의 차이가 {BACK_H}시간 전 {g0:.1f}M에서 {g:.1f}M로 줄었습니다."
    return on, r


def alerts(role, now=None):
    """Newly-on alerts for this hour (once per episode) and the race block for the manager's record."""
    now = now or time.time()
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    on, r = checks(db, now, role)
    try:
        seen = json.loads(STATE[role].read_text())
    except (OSError, ValueError):
        seen = {}
    new = [txt for k, txt in on.items() if k not in seen]
    STATE[role].write_text(json.dumps({k: seen.get(k, now) for k in on}))
    r["active_alerts"] = list(on.values())
    return new, r


if __name__ == "__main__":
    import sys
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    t = time.time() if len(sys.argv) < 2 else calendar.timegm(tuple(int(v) for v in sys.argv[1].split(",")) + (0, 0)) - KST
    for role in ("buyer", "shop"):
        on, r = checks(db, t, role)
        print(role, json.dumps(on, ensure_ascii=False, indent=1))
    print(json.dumps(r, ensure_ascii=False, indent=1))
