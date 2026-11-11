"""The regular report, short (yms979 11-11, user: 'total rank, shop rank, buyer rank, the revenue gap to inextro and
whether it is closing, the book gap and whether it is closing, well laid out; a lot of information does not tell me
what to look at').

    .venv/bin/python ops/brief.py              # markdown for the chat report
    .venv/bin/python ops/brief.py --text       # plain text with blank lines (the hourly phone push, summary.py)
    .venv/bin/python ops/brief.py --json
    .venv/bin/python ops/brief.py --hours 3    # trend window (default 6 h)

What it answers, in this order and nothing else:
  ranks      class total, shop, buyer: rank, score, the neighbour that matters and whether that gap grew or shrank
  shop races book, revenue, stars among the students' shops: the shop we race in each part (the one right behind when
             we lead, the one right ahead when we trail), the gap, how fast it moves and where it lands at the end
  buyer      pace of the last 2 h (deals/h and per-deal value) and the saved rank
  the end    the final class rank projected part by part (ops/final_rank.py)
  to check   only what needs the user's eye (shop dead, kit down, guard fires, a thin lead); 'nothing' otherwise
Trend = the live board against the archived board snapshot nearest `hours` ago (data/market_archive.db, one every
15 min). Read-only, no model call.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ops"))
import board_api  # noqa: E402
import status  # noqa: E402
import trendfit  # noqa: E402

NAME = "yms979"
END = 1794927540                       # 2026-11-17 23:59 KST
DB = HERE / "data" / "market_archive.db"
KST = timezone(timedelta(hours=9))
PARTS = (("book", "장부", 0.5, 1e6), ("revenue", "매출", 0.3, 1e6), ("stars_rank", "별점", 0.2, 1))


def kst(t, fmt="%m/%d %H:%M"):
    return datetime.fromtimestamp(t, KST).strftime(fmt)


def old_boards(now, hours):
    """[(ts, board)] oldest first: the archived snapshot nearest `hours` ago (at least 1 h old) and every later one,
    at most 30, each with our history intact (see board_api.sane)."""
    out = []
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
        ts = sorted(r[0] for r in db.execute("SELECT ts FROM board WHERE ts <= ?", (now - 600,)))
        old = [t for t in ts if t <= now - 3600]
        if not old:
            return []
        t0 = min(old, key=lambda t: abs(t - (now - hours * 3600)))
        keep = [t for t in ts if t >= t0]
        keep = keep[::max(1, len(keep) // 30)]
        for t in keep:
            b = json.loads(db.execute("SELECT data FROM board WHERE ts = ?", (t,)).fetchone()[0])
            if board_api.sane(b):
                out.append((t, b))
    except Exception:  # noqa: BLE001
        pass
    return out


def gap_series(field, me_shop, rival_shop, olds, now, live_gap):
    """[(t, our value - the rival's)] for one shop part. The book uses the guard's own readings when it watches the
    same runner-up (6.5 h, every 15 min: the slope the guard and the price controller act on)."""
    if field == "book":
        bh = (status.read(".market/guard_state.json", {}) or {}).get("book_hist") or []
        bh = [h for h in bh if h.get("rival") == rival_shop]
        if len(bh) >= 8 and bh[-1]["t"] - bh[0]["t"] >= 4 * 3600:
            return [(h["t"], h["us"] - h["rb"]) for h in bh]
    pts = []
    for t, b in olds:
        sh = {s["name"]: s for s in b["shops"]}
        if sh.get(me_shop, {}).get(field) is not None and sh.get(rival_shop, {}).get(field) is not None:
            pts.append((t, sh[me_shop][field] - sh[rival_shop][field]))
    return pts + [(now, live_gap)]


def students(b):
    """player -> (player record, its shop record, its buyer record) for the students on the board."""
    sh = {s["name"]: s for s in b["shops"]}
    bu = {x["name"]: x for x in b["buyers"]}
    return {p["player"]: (p, sh.get((p.get("shop") or {}).get("name")) or {}, bu.get((p.get("buyer") or {}).get("name")) or {})
            for p in b["players"] if p.get("student")}


def moving(gap, gap0, eps):
    """Words for a gap seen from our side (+ = we are ahead): how it moved between two readings."""
    if gap0 is None:
        return "추세 측정 중", 0
    d = gap - gap0
    if abs(d) <= eps:
        return "그대로", 0
    if gap >= 0:
        return ("더 벌리는 중", 1) if d > 0 else ("따라잡히는 중", -1)
    return ("따라잡는 중", 1) if d > 0 else ("더 뒤처지는 중", -1)


def score_race(S, S0, key, label):
    """Rank on a class score and the neighbours that matter: the one right above and the one right below, each with
    the gap now, the gap at the start of the window and which way it moved (under .004 = one small rank step = same)."""
    L = sorted((p for p in S if S[p][0].get(key) is not None), key=lambda p: -S[p][0][key])
    i = L.index(NAME)
    mine = S[NAME][0][key]
    mine0 = S0[NAME][0].get(key) if S0 and NAME in S0 else None
    out = {"label": label, "rank": i + 1, "of": len(L), "score": mine,
           "change": None if mine0 is None else round(mine - mine0, 3), "near": []}
    for j, role in ((i - 1, "위"), (i + 1, "아래")):
        if 0 <= j < len(L):
            q = L[j]
            gap = mine - S[q][0][key]
            gap0 = (mine0 - S0[q][0][key]) if mine0 is not None and q in S0 and S0[q][0].get(key) is not None else None
            word, sign = moving(gap, gap0, 0.004)
            out["near"].append({"role": role, "rank": j + 1, "player": q, "score": S[q][0][key], "gap": round(gap, 3),
                                "gap_before": None if gap0 is None else round(gap0, 3), "trend": word, "good": sign})
    return out


def part_race(S, S0, field, label, weight, olds, now, left):
    """One shop part among the students' shops: our rank, the shop we race (right behind when we lead, right ahead
    when we trail), the gap, its fitted speed (trendfit: flat unless the slope is over 2 standard errors) and where
    it lands at the round's end."""
    vals = {p: S[p][1].get(field) for p in S if S[p][1].get(field) is not None and S[p][1].get("deals")}
    if NAME not in vals:
        return None
    L = sorted(vals, key=lambda p: -vals[p])
    i = L.index(NAME)
    rank = 1 + sum(1 for p in vals if vals[p] > vals[NAME])
    out = {"field": field, "label": label, "weight": weight, "rank": rank, "of": len(L), "value": vals[NAME]}
    j = i + 1 if rank == 1 else i - 1
    if field == "stars_rank":                      # ties are common: race the next DIFFERENT value up (down when 1st)
        ups = [p for p in L if vals[p] > vals[NAME]]
        downs = [p for p in L if vals[p] < vals[NAME]]
        q = (ups[-1] if ups else (downs[0] if downs else None))
        j = L.index(q) if q else -1
    if not 0 <= j < len(L):
        return out
    q = L[j]
    gap = vals[NAME] - vals[q]
    out.update(rival=S[q][1].get("name") or q, rival_player=q, rival_rank=1 + sum(1 for p in vals if vals[p] > vals[q]),
               rival_value=vals[q], gap=gap)
    if field == "stars_rank":                      # the board rounds it to 2 decimals: compare the two ends only
        v0 = lambda p: (S0[p][1].get(field) if S0 and p in S0 else None)
        gap0 = (v0(NAME) - v0(q)) if v0(NAME) is not None and v0(q) is not None else None
        word, sign = moving(gap, gap0, 0.005)
        out.update(trend=word, good=sign, value_before=v0(NAME))
        return out
    fit = trendfit.linfit(gap_series(field, S[NAME][1].get("name"), out["rival"], olds, now, gap))
    if not fit or fit["span_h"] < 1:
        out.update(trend="추세 측정 중", good=0)
        return out
    flat = fit["flat"] or abs(fit["slope"] * fit["span_h"]) < {"book": 1e6, "revenue": 5e6}[field]
    per_h = 0.0 if flat else fit["slope"]
    word, sign = ("거의 그대로", 0) if flat else moving(gap, gap - per_h, 0)
    out.update(trend=word, good=sign, per_h=per_h, span_h=round(fit["span_h"], 1), lo=fit["lo"], hi=fit["hi"],
               gap_end=gap + per_h * left, sd_end=(fit["se"] or 0.0) * left)
    if gap * out["gap_end"] < 0 and per_h:         # the order flips before the end at this speed
        out["flip_h"] = -gap / per_h
    if gap < 0 and left > 0:
        out["need_per_h"] = -gap / left            # how much faster than the rival we must sell to pass it by the end
    return out


def build(hours=6.0):
    now = time.time()
    b = board_api.fetch(timeout=20, tries=2, wait=10)
    olds = old_boards(now, hours)
    S, S0 = students(b), (students(olds[0][1]) if olds else {})
    span = (now - olds[0][0]) / 3600 if olds else None
    left = max(0.0, (END - now) / 3600)
    me = S[NAME]
    out = {"t": now, "kst": kst(now), "hours_left": round(left, 1), "window_h": None if span is None else round(span, 1),
           "ranks": [score_race(S, S0, "student_total", "전체"), score_race(S, S0, "student_shop", "상점"),
                     score_race(S, S0, "student_buyer", "buyer")],
           "parts": [r for r in (part_race(S, S0, f, lab, w, olds, now, left) for f, lab, w, _ in PARTS) if r]}
    # buyer: saved rank, the last 2 h of our own deals, the projected final mean a deal has to beat
    sv = {p: S[p][2].get("saved") for p in S if S[p][2].get("saved") is not None}
    sv0 = {p: S0[p][2].get("saved") for p in S0 if S0[p][2].get("saved") is not None} if S0 else {}
    rk = lambda d: 1 + sum(1 for x in d.values() if x > d[NAME])
    sc = status.read(".market/buyer_score.json", {}) or {}
    out["buyer"] = {"saved": me[2].get("saved"), "saved_before": sv0.get(NAME), "saved_rank": rk(sv),
                    "saved_rank_before": rk(sv0) if NAME in sv0 else None, "of": len(sv), "m_star": sc.get("m_star"),
                    "deals": me[2].get("deals"), "pace_2h": status.buyer_pace(now, 2), "pace_6h": status.buyer_pace(now, 6)}
    try:
        import final_rank
        comp = final_rank.project_components(hours=hours, board=b)
        if comp:
            mine = next(r for r in comp["rows"] if r["player"] == NAME)
            out["end"] = {"rank": comp["us"]["rank_end"], "total": comp["us"]["total_end"], "shop": mine["shop"],
                          "buyer": mine["buyer"], "window_h": comp["window_h"],
                          "top": [(r["rank_end"], r["player"], r["total_end"]) for r in comp["rows"][:4]]}
    except Exception as e:  # noqa: BLE001
        out["end_error"] = repr(e)[:120]
    # what needs the user's eye: (level, text), level "warn" or "info"
    chk = []
    shop, kit, gs = me[1], status.kit_state(), status.read(".market/guard_state.json", {}) or {}
    task_age = None
    try:                                           # the kit log stamps HH:MM:SS (UTC)
        h, m, s_ = (int(x) for x in kit["last_seller_task"].split(":"))
        task_age = (now - (now // 86400 * 86400 + h * 3600 + m * 60 + s_)) % 86400 / 60
        if task_age > 1435:                        # a task stamped in the same second as `now`: not a day old
            task_age = 0.0
    except Exception:  # noqa: BLE001
        pass
    live = [s for s in b["shops"] if s.get("deals")]
    dead = sum(1 for s in live if not s.get("alive"))
    if not kit.get("kit_running"):
        chk.append(("warn", "상점 프로그램(kit)이 꺼져 있음"))
    elif task_age is None or task_age > 10:
        chk.append(("warn", f"상점에 {'오랫동안' if task_age is None else f'{task_age:.0f}분째'} 주문이 안 들어옴"))
    if dead >= 0.3 * len(live):
        ok = task_age is not None and task_age <= 10
        chk.append(("info", f"시장 장애 중: 상점 {len(live)}곳 중 {dead}곳 정지. 우리 상점은 "
                            + ("주문을 받고 있음 (주소 교대 재등록)" if ok else "주문이 끊김")))
    for f in (gs.get("fires") or []):
        chk.append(("warn", "경보: " + str(f)[:110]))
    for n in out["ranks"][0]["near"]:
        if n["role"] == "아래" and n["gap"] < 0.03:
            chk.append(("warn", f"전체 {n['rank']}위 {n['player']}와 차이 {n['gap']:.3f} (0.03 미만)"))
    for p in out["parts"]:
        if p.get("flip_h") and p["gap"] > 0:
            chk.append(("warn", f"{p['label']}: 이 속도면 약 {p['flip_h']:.0f}시간 뒤 {p['rival']}에게 역전당함"))
    bk = next((p for p in out["parts"] if p["field"] == "book"), None)
    if bk and bk["rank"] == 1 and bk.get("gap_end") is not None and 0 < bk["gap_end"] < 150e6 and not bk.get("flip_h"):
        chk.append(("warn", f"장부: 종료 시 예상 차이 {bk['gap_end'] / 1e6:.0f}M (150M 미만이면 가격 조절기가 가격을 올림)"))
    p2 = out["buyer"]["pace_2h"]
    if p2.get("v") is not None and out["buyer"]["m_star"] and p2["v"] < out["buyer"]["m_star"]:
        chk.append(("warn", f"buyer 건당 값 {p2['v']}이 기준({out['buyer']['m_star']:.3f})보다 낮음: saved가 내려가는 중"))
    e = out.get("end")
    if e and e["rank"] > out["ranks"][0]["rank"]:
        chk.append(("warn", f"지금 추세 그대로면 종료 시 전체 {e['rank']}위로 내려감 (종료 예상 참고)"))
    out["check"] = chk
    out["shop_state"] = {"alive": shop.get("alive"), "kit": kit.get("kit_running"), "last_task_min": task_age}
    pc = status.read(".market/price_ctl.json", {}) or {}
    if pc:
        adj = pc.get("sku") or {}
        out["price_ctl"] = {"under": pc.get("under"), "dearer": sum(1 for x in adj.values() if x > 0),
                            "cheaper": sum(1 for x in adj.values() if x < 0)}
    try:
        import model_env
        sp = model_env.spend_summary(now)
        out["api"] = {"day": sp.get("today"), "credit": sp.get("credit_left")}
    except Exception:  # noqa: BLE001
        pass
    return out


# ---- wording --------------------------------------------------------------------------------------------------
MARK = {1: "🟢", -1: "🔴", 0: "⚪"}
ICON = {"warn": "⚠️", "info": "ℹ️"}


def _amt(x, field):
    return f"{x:+.2f}" if field == "stars_rank" else f"{x / 1e6:+,.0f}M"


def part_words(p):
    """(the shop we race and the gap, the trend in one sentence) for one shop part."""
    f = p["field"]
    if "rival" not in p:
        return "경쟁 상점 없음", ""
    who = f"{p['rival_rank']}위 {p['rival']}보다 {_amt(p['gap'], f)}"
    if f == "stars_rank":
        return who, f"{p['trend']} (우리 {p['value']:.2f}, 상대 {p['rival_value']:.2f})"
    if "per_h" not in p:
        return who, p.get("trend", "")
    t = p["trend"]
    if not p["good"]:
        t += f" ({p['span_h']:.0f}시간 동안 {p['lo'] / 1e6:+,.0f}~{p['hi'] / 1e6:+,.0f}M 사이)"
    else:
        t += f" (시간당 {abs(p['per_h']) / 1e6:,.1f}M)"
    if p.get("flip_h"):
        when = kst(time.time() + p["flip_h"] * 3600, "%m/%d %H시")
        t += f" → 이 속도면 약 {p['flip_h']:.0f}시간 뒤({when}) " + ("역전" if p["gap"] < 0 else "역전당함")
    elif p["good"]:
        t += f" → 종료 시 {_amt(p['gap_end'], f)} 예상"
        if f == "book" and p.get("sd_end"):
            t += f" (오차 ±{p['sd_end'] / 1e6:.0f}M)"
    if p["gap"] < 0 and not p.get("flip_h"):
        t += f". 역전하려면 상대보다 시간당 {p['need_per_h'] / 1e6:,.1f}M 더 팔아야 함"
    return who, t


def near_words(r, window):
    out = []
    for n in r["near"]:
        before = f", {window:.0f}시간 전 {n['gap_before']:+.3f}" if n.get("gap_before") is not None and window else ""
        out.append((n["good"], f"{n['role']} {n['rank']}위 {n['player']} ({n['gap']:+.3f}{before}) {n['trend']}"))
    return out


def headline(o):
    t, s, bu = o["ranks"]
    return f"전체 {t['rank']}위 · 상점 {s['rank']}위 · buyer {bu['rank']}위"


def markdown(o):
    w = o["window_h"]
    L = [f"## 정기 보고 · {o['kst']} KST · 종료까지 {o['hours_left']:.0f}시간", "",
         f"**{headline(o)}**", "",
         "### 1. 순위" + (f" (괄호 안: 지금 차이, {w:.0f}시간 전 차이)" if w else ""), "",
         "| 구분 | 순위 | 점수 | 변화 | 가까운 경쟁자 |", "|---|---|---|---|---|"]
    for r in o["ranks"]:
        near = "<br>".join(f"{MARK[g]} {txt}" for g, txt in near_words(r, w)) or "-"
        ch = "-" if r["change"] is None else f"{r['change']:+.3f}"
        L.append(f"| **{r['label']}** | **{r['rank']}위** / {r['of']} | {r['score']:.3f} | {ch} | {near} |")
    L += ["", "### 2. 상점 경쟁 (항목별)", "", "| 항목 | 우리 순위 | 경쟁 상점과 차이 | 추세 |", "|---|---|---|---|"]
    for p in o["parts"]:
        who, t = part_words(p)
        L.append(f"| **{p['label']}** ({p['weight']:.0%}) | {p['rank']}위 | {who} | {MARK[p.get('good', 0)]} {t} |")
    pc = o.get("price_ctl")
    if pc:
        u = pc["under"] or 0
        lvl = f"경쟁 상점보다 {abs(u):.2f} {'아래' if u > 0 else '위'}" if u else "경쟁 상점과 같은 가격"
        L += ["", f"가격 조절기: {lvl} (정가 대비), 더 비싸게 파는 품목 {pc['dearer']}개, 더 싸게 {pc['cheaper']}개"]
    bu, p2 = o["buyer"], o["buyer"]["pace_2h"]
    L += ["", "### 3. buyer", ""]
    sr = f"{bu['saved_rank']}위" + (f" ({bu['saved_rank_before']}위에서)" if bu.get("saved_rank_before") not in (None, bu["saved_rank"]) else "")
    sd = f" ({bu['saved'] - bu['saved_before']:+.4f})" if bu.get("saved_before") is not None else ""
    L.append(f"- saved {bu['saved']:.4f}{sd}, 반 {sr}")
    L.append(f"- 최근 2시간: 시간당 **{p2['deals_h']}건**, 건당 절약 **{p2['v']}** (식품 {p2['food_h']}건 {p2['food_v']} / 가구 {p2['goods_h']}건 {p2['goods_v']})")
    if bu.get("m_star"):
        L.append(f"- 건당 절약이 {bu['m_star']:.3f}보다 높으면 saved가 오름")
    if o.get("end"):
        e = o["end"]
        others = ", ".join(f"{r}위 {p} {t:.2f}" for r, p, t in e["top"] if p != NAME)
        L += ["", f"### 4. 종료 시 예상 (최근 {e['window_h']:.0f}시간 추세 그대로일 때)", "",
              f"- **우리 {e['rank']}위** ({e['total']:.3f}): 상점 {e['shop'][0]:.3f} → {e['shop'][1]:.3f}, buyer {e['buyer'][0]:.3f} → {e['buyer'][1]:.3f}",
              f"- 다른 학생: {others}", "- 차이 0.03 이내는 동률로 봐야 함"]
    L += ["", "### 5. 확인할 것", ""]
    L += [f"- {ICON[lv]} {c}" for lv, c in o["check"]] or ["- 없음 (상점 정상 운영 중)"]
    if o.get("api"):
        L += ["", f"API: 24시간 ${o['api']['day']}, 남은 크레딧 약 ${o['api']['credit']:.0f}"]
    return "\n".join(L)


def text(o):
    """Plain text for the phone: short lines, one blank line between blocks, what to check first."""
    w = o["window_h"]
    L = [f"{o['kst']} KST · 종료까지 {o['hours_left']:.0f}시간"]
    if o["check"]:
        L += [""] + [f"{ICON[lv]} {c}" for lv, c in o["check"]]
    for r in o["ranks"]:
        ch = "" if not r["change"] else f" ({r['change']:+.3f})"
        L += ["", f"[{r['label']}] {r['rank']}위 · {r['score']:.3f}{ch}"]
        L += [f"  {MARK[g]} {txt}" for g, txt in near_words(r, w)]
        if r["label"] == "상점":
            for p in o["parts"]:
                who, t = part_words(p)
                L += ["", f"  {p['label']} {p['rank']}위: {who}", f"  {MARK[p.get('good', 0)]} {t}"]
        if r["label"] == "buyer":
            p2 = o["buyer"]["pace_2h"]
            L.append(f"  최근 2시간: 시간당 {p2['deals_h']}건, 건당 {p2['v']}")
            L.append(f"  saved {o['buyer']['saved']:.4f} (반 {o['buyer']['saved_rank']}위)")
    if o.get("end"):
        e = o["end"]
        L += ["", f"[종료 예상] 우리 {e['rank']}위 ({e['total']:.3f})",
              f"  상점 {e['shop'][0]:.3f} → {e['shop'][1]:.3f}, buyer {e['buyer'][0]:.3f} → {e['buyer'][1]:.3f}",
              "  " + ", ".join(f"{r}위 {p} {t:.2f}" for r, p, t in e["top"] if p != NAME)]
    if not o["check"]:
        L += ["", "확인할 것: 없음"]
    return "\n".join(L)


def main():
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 6.0
    o = build(hours)
    if "--json" in sys.argv:
        print(json.dumps(o, ensure_ascii=False, indent=1, default=str))
    elif "--text" in sys.argv:
        print(text(o))
    else:
        print(markdown(o))


if __name__ == "__main__":
    main()
