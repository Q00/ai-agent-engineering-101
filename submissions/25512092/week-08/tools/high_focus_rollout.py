"""High-price focus (person's decision 11/09 ~14:35), applied right after the 15:05 checks (the STOCK_CAP 30M 3 h report,
"report sent" in logs/cap30_rollout.log, and the lock-release 3 h report, "lock report sent" in logs/restart_1400.log;
at the latest 15:40):
  1. restock lines after the 4 request-based lines by expected profit (line_rank.json; only with --rank, i.e. when
     the simulation sent before was positive)
  2. high_max 40 -> 60, high_share 0.6 -> 0.7 (policy.json + the QC policy table; the QC / CEO automatic rules'
     ceilings 0.7 / 60 in high_caps.json)
  3. sofa limits x2 (sofa_rules.json "x2": 2 a window, 20 unsold, 2 lines, 1.0M a unit)
Unchanged: the high-price buy guard, first ask <= market avg, floor max(cost x 1.05, median x 0.8), unit cost cap
1.0M, the B grade warranty rule, G5e3784e6 out, C grade sofas without a warranty, the STOCK_CAP brakes and the
analyst's 3 h check (market-adjusted).
High-price brake, every 15 min: any of
  a. median time from supply to sale of the high-price units sold in the last 6 h > 6 h (at least 3 sales)
  b. (11/09 ~14:45, was "12 h+ unsold > 5M") of the high-price units supplied after the switch and older than 12 h,
     the share of their cost still unsold > 30% (judged from 10 such units; the units held before are left out)
  c. high-price receipts / negotiations in the last 3 h (at least 10) <= half of the 24 h before the switch
-> high_max 40, high_share 0.6 (policy, QC table, ceilings 0.6 / 40) at once and an urgent Telegram.
3 h later one Telegram, 3 h after vs 3 h before.
    python tools/high_focus_rollout.py [--rank] [--now] [--report-dry]      (log in logs/high_focus_rollout.log)
"""
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

LOG = os.path.join(HERE, "logs", "high_focus_rollout.log")
STATE = os.path.join(HERE, "logs", "high_focus_state.json")
LATEST = datetime(2026, 11, 9, 15, 40).timestamp()
FROM = datetime(2026, 11, 9, 15, 0).timestamp()
CHECK_S, H = 900, 3
SELL_MEDIAN_H, STALE_H, STALE_SHARE, STALE_MIN_N, REC_DROP = 6, 12, 0.30, 10, 1 / 3   # 11/09 ~14:45: was 5M unsold
# 11/09 ~23:55 (person, overnight): receipt ratio <= 1/3 of the base (was 1/2); a trip needs CONSECUTIVE checks in a
# row with a hit (the first: a Telegram warning only, decision high_brake_warn)
CONSECUTIVE = 2
SOFA_CAT = "소파/의자/스툴"
ME = "choo-chu"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def _sent(path, tag):
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if tag in ln:
                    try:
                        t = datetime.strptime(f"2026-{ln[:14]}", "%Y-%m-%d %H:%M:%S").timestamp()
                    except ValueError:
                        continue
                    if t >= FROM:
                        return True
    except OSError:
        pass
    return False


def ready():
    return (_sent(os.path.join(HERE, "logs", "cap30_rollout.log"), "report sent")
            and _sent(os.path.join(HERE, "logs", "restart_1400.log"), "lock report sent"))


def db():
    return sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)


def high_min():
    from depts.qc import HIGH_MIN
    return HIGH_MIN


def rec_ratio(d, t0, t1):
    n, r = d.execute("SELECT COUNT(*), COALESCE(SUM(outcome='receipt'),0) FROM shop_deals WHERE ts>=? AND ts<? AND cost>?",
                     (t0, t1, high_min())).fetchone()
    return (r / n if n else None), n


def excluded_units():
    """(11/09 ~14:50) over-cost high units (depts/overcost.py: listed or seen) are out of the brake."""
    try:
        from depts import overcost
        return set(overcost.rules().get("units") or []) | set(overcost.seen())
    except Exception:
        return set()


def brake_state(d, now, base_ratio, since=None):
    """[(name, tripped, text)] for the three conditions (over-cost units left out of a and b)."""
    hm = high_min()
    ex = excluded_units()
    sold = d.execute("SELECT s.ts - m.supplied_at, m.unit FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND "
                     "s.ts>=? AND m.cost>? AND m.supplied_at IS NOT NULL", (ME, now - SELL_MEDIAN_H * 3600, hm)).fetchall()
    secs = [x for x, u in sold if x is not None and x >= 0 and u not in ex]
    med = statistics.median(secs) / 3600 if secs else None
    a = (len(secs) >= 3 and med > SELL_MEDIAN_H,
         f"고가 입고->판매 중앙값(최근 6시간 판매 {len(secs)}건) {'-' if med is None else f'{med:.1f}시간'} (기준 {SELL_MEDIAN_H}시간 초과"
         f"{', 3건 미만이면 판단 보류' if len(secs) < 3 else ''})")
    # 11/09 ~14:45 (person): of the high-price units supplied after the switch that are older than 12 h, the share of
    # their cost still unsold > 30% (at least 10 such units); the units held before the switch are left out
    if since:
        aged = d.execute("SELECT unit, cost FROM my_stock WHERE cost>? AND supplied_at>=? AND supplied_at<=?",
                         (hm, since, now - STALE_H * 3600)).fetchall()
        aged = [(u, c) for u, c in aged if u not in ex]
    else:
        aged = []
    held = held_units()
    held_ids = {u.get("unit") for u in held} if held is not None else None
    if held_ids is None:                         # no inventory file: unsold = no sale of the unit by my shop
        held_ids = {u for u, _ in aged if not d.execute("SELECT 1 FROM settles WHERE unit=? AND shop=?", (u, ME)).fetchone()}
    tot = sum(c or 0 for _, c in aged)
    left = sum(c or 0 for u, c in aged if u in held_ids)
    share = left / tot if tot else None
    b = (len(aged) >= STALE_MIN_N and share is not None and share > STALE_SHARE,
         f"적용 후 입고 고가 중 12시간 지난 {len(aged)}개 원가 {tot:,} 가운데 미판매 {left:,} "
         f"({'-' if share is None else f'{share:.0%}'}, 기준 {STALE_SHARE:.0%} 초과"
         f"{f', {STALE_MIN_N}개 미만이면 판단 보류' if len(aged) < STALE_MIN_N else ''})")
    r, n = rec_ratio(d, now - 3 * 3600, now)
    c = (n >= 10 and base_ratio and r is not None and r <= REC_DROP * base_ratio,
         f"고가 협상 대비 영수증 최근 3시간 {'-' if r is None else f'{r:.1%}'} ({n}건) vs 적용 전 24시간 "
         f"{'-' if not base_ratio else f'{base_ratio:.1%}'} (3분의 1 이하면 발동)")
    return [("sell_median", *a), ("stale_cost", *b), ("receipt_ratio", *c)]


def held_units():
    try:
        with open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
            return [u for u in json.load(f).get("units", []) if u.get("grade") != "expired"]
    except (OSError, ValueError):
        return None


def catalog():
    try:
        with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def window(d, t0, t1, cat):
    """Numbers for the 3 h report in [t0, t1)."""
    from depts import sofa
    hm = high_min()
    leaf = lambda k: ((cat.get(k) or {}).get("category") or "").split(">")[-1]
    is_sofa = lambda sku, cost: leaf(sku) == SOFA_CAT and (cost or 0) >= sofa.MIN_COST
    grp = lambda sku, cost: "sofa" if is_sofa(sku, cost) else "high" if (cost or 0) > hm else "low"
    out = {g: {"buy_n": 0, "buy_cost": 0, "n": 0, "rev": 0, "cost": 0} for g in ("high", "sofa", "low")}
    for sku, cost in d.execute("SELECT sku, cost FROM my_stock WHERE supplied_at>=? AND supplied_at<?", (t0, t1)):
        g = out[grp(sku, cost)]
        g["buy_n"] += 1
        g["buy_cost"] += cost or 0
    sales = d.execute("SELECT s.sku, s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? "
                      "AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    for sku, amt, cost in sales:
        g = out[grp(sku, cost)]
        g["n"] += 1
        g["rev"] += amt or 0
        g["cost"] += cost or 0
    rev = sum(a or 0 for _, a, _ in sales)
    out["all"] = {"n": len(sales), "rev": rev, "aov": rev / len(sales) if sales else 0,
                  "big": sum(1 for _, a, _ in sales if (a or 0) >= 500_000) / len(sales) if sales else 0}
    return out


def report(t, now=None, state=None):
    now = now or time.time()
    t1 = min(t + H * 3600, now)
    cat = catalog()
    d = db()
    try:
        a, b = window(d, t, t1, cat), window(d, t - (t1 - t), t, cat)
        st = brake_state(d, now, (state or load_state()).get("base_ratio"), (state or load_state()).get("since"))
    finally:
        d.close()
    hm = high_min()
    held = held_units() or []
    hh = [u for u in held if u.get("cost", 0) > hm]
    pc = lambda g: f"{(g['rev'] - g['cost']) / g['cost']:+.1%}" if g["cost"] else "-"
    L = [f"고가 집중 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, 후 vs 전)"]
    for g, name in (("high", "고가(소파 제외)"), ("sofa", "소파")):
        x, y = a[g], b[g]
        L.append(f"- {name}: 매입 {x['buy_n']}개 {x['buy_cost']:,} vs {y['buy_n']}개 {y['buy_cost']:,}; 판매 {x['n']} vs {y['n']}, "
                 f"매출 {x['rev']:,} vs {y['rev']:,}, 원가 대비 이익 {pc(x)} vs {pc(y)}")
    L.append(f"- 고가 재고(지금) 원가 {sum(u.get('cost', 0) for u in hh):,} {len(hh)}개")
    L.append(f"- 객단가 {int(a['all']['aov']):,} vs {int(b['all']['aov']):,}, 50만 이상 판매 비중 {a['all']['big']:.1%} vs "
             f"{b['all']['big']:.1%}, 전체 매출 {a['all']['rev']:,} ({a['all']['n']}건) vs {b['all']['rev']:,} ({b['all']['n']}건)")
    s = load_state()
    L.append("- 고가 브레이크: " + ("발동됨 " + s["tripped"] if s.get("tripped") else "정상") + "; "
             + "; ".join(txt for _, _, txt in st))
    return "\n".join(L)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    s = load_state()
    s.update(kw)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)


def set_high(share, cap, why):
    import decisions
    import policy
    from depts import high_caps
    from market_db import connect
    old = (policy.get("purchasing", "high_share"), policy.get("purchasing", "high_max"))
    v = policy.write({"purchasing": {"high_share": share, "high_max": cap}}, why, by="person")
    high_caps.set_caps(share, cap, why)
    c = connect()
    try:
        c.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), share, cap, why[:200]))
        c.commit()
    finally:
        c.close()
    decisions.record("purchasing", "high_split", "purchasing.high_share/high_max", [list(old), [share, cap]],
                     f"{why}; policy v{v.get('version')}")
    return old, v.get("version")


def apply(rank):
    import decisions
    from depts import line_rank, sofa
    d = db()
    try:
        base, base_n = rec_ratio(d, time.time() - 86400, time.time())
    finally:
        d.close()
    if rank:
        with open(line_rank.RULES, "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "since": round(time.time()), "horizon_h": line_rank.HORIZON_H,
                       "note": "person 11/09 ~14:35: lines after the request lines by expected profit"}, f)
        decisions.record("purchasing", "line_rank_on", "line_rank", True, "person 11/09 ~14:35, simulation positive")
    r = sofa.rules()
    r.update(x2=True, x2_since=round(time.time()))
    with open(sofa.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    decisions.record("purchasing", "sofa_x2", "sofa_rules", sofa.limits_now(r), "person 11/09 ~14:35: sofa limits x2")
    old, v = set_high(0.7, 60, "person 11/09 ~14:35 (high focus): high_share 0.6 -> 0.7, high_max 40 -> 60")
    t = time.time()
    save_state(since=round(t), base_ratio=base, base_n=base_n, rank=rank, tripped=None)
    log(f"applied: rank {rank}, sofa x2 {sofa.limits_now(r)}, high {old} -> (0.7, 60) policy v{v}; base receipt ratio {base} ({base_n})")
    return t, base, base_n, old, v


def old_stale(now=None):
    """(11/09 ~14:45) the high-price units held > 12 h unsold before the switch: left out of the brake; for the 15:05
    report: SKU, name, age, floor (the high-price floor rule; sofas their own), market average and floor / average."""
    from depts import high_ask, sofa
    from market_db import load_stats, price_of
    now = now or time.time()
    hm = high_min()
    stats = load_stats()
    rows = []
    for u in held_units() or []:
        age = (now - (u.get("supplied_at") or now)) / 3600
        if u.get("cost", 0) <= hm or age <= STALE_H:
            continue
        avg, n = price_of(u["sku"], stats)
        if sofa.applies(u):
            fl = (sofa.floor(u, now) or (None,))[0]
        else:
            fl = high_ask.high_floor(u, now)
        rows.append({"unit": u.get("unit"), "sku": u["sku"], "name": (u.get("name") or "")[:24], "grade": u.get("grade"),
                     "age_h": round(age, 1), "cost": u.get("cost"), "floor": fl, "avg": int(avg) if avg else None})
    return sorted(rows, key=lambda r: -r["age_h"])


def old_stale_text(rows):
    tot = sum(r["cost"] or 0 for r in rows)
    L = [f"브레이크 계산에서 뺀 기존 12시간+ 미판매 고가 {len(rows)}개 (원가 {tot:,}): SKU / 나이 / 원가 / 하한 / 시장 평균 (하한÷평균)"]
    for r in rows:
        ratio = f"{r['floor'] / r['avg']:.0%}" if r["floor"] and r["avg"] else "-"
        fl = "-" if r["floor"] is None else f"{r['floor']:,}"
        av = "-" if r["avg"] is None else f"{r['avg']:,}"
        L.append(f"- {r['sku']} {r['name']} ({r['grade']}): {r['age_h']:.0f}시간, 원가 {r['cost']:,}, 하한 {fl}, "
                 f"평균 {av} ({ratio})")
    return "\n".join(L)


def overcost_on(rows):
    """(11/09 ~14:50) the listed units whose floor is above the market average: floor cost x 1.00, out of high_max and
    the brake; from now on any high unit with cost > avg too (depts/overcost.py). The seller loads high_ask at a
    restart: restart it."""
    import alerts
    from depts import overcost
    from seller_conc_rollout import restart_seller, seller_back
    over = [r for r in rows if r["floor"] and r["avg"] and r["floor"] > r["avg"]]
    with open(overcost.RULES, "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "since": round(time.time()), "units": [r["unit"] for r in over],
                   "note": "person 11/09 ~14:50: floor > avg at the 15:05 list + any high unit with cost > avg: floor "
                           "cost x 1.00, out of high_max and the high-price brake"}, f, ensure_ascii=False)
    units = held_units() or []
    from market_db import load_stats, price_of
    st = load_stats()
    oc = overcost.mark(units, lambda k: price_of(k, st)[0], log=log)
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"overcost on: {len(over)} listed, {len(oc)} over-cost held; seller restart: killed {killed}, back {back}")
    alerts.send("info", "overcost_on", f"원가가 시장 평균보다 높은 고가 규칙 적용 {datetime.now():%H:%M}: 목록 중 하한>평균 {len(over)}개 "
                f"({', '.join(r['sku'] for r in over)}) + 지금 원가>평균인 고가, 합 {len(oc)}개: 하한 원가 x 1.05 -> 1.00 (원가 아래 없음), "
                "high_max 개수와 고가 브레이크에서 제외, 앞으로 원가>평균이 되는 고가도 자동 (decision overcost_high). 나머지(하한<평균)는 그대로. "
                f"seller 재시작 {'완료' if back else '확인 안 됨'}", log=log)


def warn(why, streak):
    """A brake condition hit, not yet CONSECUTIVE checks in a row: one Telegram warning, nothing changes."""
    import alerts
    import decisions
    decisions.record("purchasing", "high_brake_warn", "high_focus", why, f"check {streak}/{CONSECUTIVE}: warning only")
    alerts.send("info", f"high_brake_warn:{int(time.time())}", f"고가 브레이크 경고 {datetime.now():%H:%M} ({streak}/{CONSECUTIVE}회, "
                f"아직 발동 안 함): {why}\n다음 점검({CHECK_S // 60}분 뒤)에도 걸리면 발동", log=log)
    log(f"warn {streak}/{CONSECUTIVE}: {why}")


def trip(why):
    import alerts
    import decisions
    old, v = set_high(BRAKE_SHARE, BRAKE_MAX, f"high-price brake: {why}")
    save_state(tripped=f"{datetime.now():%H:%M} {why}")
    decisions.record("purchasing", "high_brake", "high_focus", why,
                     f"high_max {BRAKE_MAX}, high_share {BRAKE_SHARE} (ceilings {BRAKE_SHARE} / {BRAKE_MAX})")
    alerts.send("urgent", "high_brake", f"고가 브레이크 발동 {datetime.now():%H:%M}: {why}\nhigh_max {old[1]} -> {BRAKE_MAX}, "
                f"high_share {old[0]} -> {BRAKE_SHARE} (policy v{v}, CEO·QC 자동 상한도 {BRAKE_SHARE}/{BRAKE_MAX}). "
                "줄 우선순위·소파 한도·HIGH_MAX는 그대로", log=log)
    log(f"brake: {why}")


BRAKE_SHARE, BRAKE_MAX = 0.7, 60     # 11/09 ~21:20 (person): the brake goes back to 0.7 / 60 (was 0.6 / 40)
# 11/10 ~12:10 (person, "shop brakes minimised"): the brake only warns (Telegram, when a hit starts and hourly while it
# lasts); high_max / high_share are never lowered by it
WARN_ONLY, WARN_EVERY_S = True, 3600


def warn_only(why, streak):
    import alerts
    import decisions
    decisions.record("purchasing", "high_brake_warn", "high_focus", why, f"warning only (person 11/10), check {streak}")
    alerts.send("info", f"high_brake_warn:{int(time.time())}", f"고가 브레이크 경고 {datetime.now():%H:%M} (경고만, high_max·"
                f"high_share 자동 하향 없음): {why}" + ("" if streak == 1 else f" ({streak}회째 연속, 1시간마다 알림)"), log=log)
    log(f"warn only (check {streak}): {why}")


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import alerts
    rank = "--rank" in sys.argv
    if "--now" not in sys.argv:
        log("waiting for the 15:05 checks (cap30 report, lock report)")
        while not ready() and time.time() < LATEST:
            time.sleep(30)
    if "--resume" in sys.argv:                  # 11/09 ~21:20: restart for the new brake values, the brake loop only
        st = load_state()
        log(f"resume: brake loop, since {st['since']}, back to {BRAKE_SHARE} / {BRAKE_MAX} on a trip")
        brake_loop(st["since"], st["base_ratio"], reported=True)
        return
    t, base, base_n, old, v = apply(rank)
    from depts import sofa
    alerts.send("info", "high_focus_on", f"고가 집중 적용 {datetime.now():%H:%M}: "
                + ("(1) restock 줄: 요청 기반 4줄 다음은 기대 이익 순 " if rank else "(1) 줄 우선순위는 적용 안 함 ")
                + f"(2) high_max {old[1]} -> 60, high_share {old[0]} -> 0.7 (policy v{v}, CEO·QC 자동 상한 0.7/60) "
                f"(3) 소파 한도 2배: 20분 창당 1 -> 2개, 미판매 10 -> 20개, 줄 1 -> 2, 단위 원가 50만 -> 100만 (수요 조건·G5e3784e6 제외·C등급 보증 없음 그대로). "
                f"고가 가드·첫 호가·하한·1.0M·B 보증·STOCK_CAP 브레이크·분석관 점검 그대로. 고가 브레이크 15분마다 (입고->판매 중앙값 6시간, "
                f"적용 후 입고 고가 중 12시간 지난 것의 미판매 원가 30% 초과(10개 이상일 때), 영수증 비율 절반; 기준 {'-' if base is None else f'{base:.1%}'} / {base_n}건). 3시간 점검 예정",
                log=log)
    try:
        rows = old_stale()
        save_state(excluded=[r["unit"] for r in rows])
        alerts.send("info", "high_focus_old_stale", old_stale_text(rows), log=log)
        overcost_on(rows)
    except Exception as e:
        log(f"old stale list / overcost failed: {type(e).__name__}: {e}")
    brake_loop(t, base, reported=False)


def brake_loop(t, base, reported):
    import alerts
    while True:
        now = time.time()
        if not load_state().get("tripped"):
            d = db()
            try:
                st = brake_state(d, now, base, t)
            finally:
                d.close()
            hit = [txt for _, x, txt in st if x]
            log("check " + " | ".join(f"{n}={'TRIP' if x else 'ok'}" for n, x, _ in st))
            streak = (load_state().get("warn_streak") or 0) + 1 if hit else 0
            save_state(warn_streak=streak)
            if WARN_ONLY:                       # 11/10 ~12:10 (person): a warning only, high_max / high_share stay
                if hit and (streak == 1 or now - (load_state().get("warn_last") or 0) >= WARN_EVERY_S):
                    save_state(warn_last=round(now))
                    warn_only("; ".join(hit), streak)
            elif hit and streak >= CONSECUTIVE:
                trip("; ".join(hit) + f" ({streak}회 연속)")
            elif hit:
                warn("; ".join(hit), streak)
        if not reported and now >= t + H * 3600 + 60:
            alerts.send("daily", f"high_focus_report:{int(t)}", report(t), log=log)
            log("report sent")
            reported = True
        if reported and load_state().get("tripped"):
            return
        if now > datetime(2026, 11, 17, 23, 59).timestamp():
            return
        time.sleep(CHECK_S if not reported else CHECK_S)


if __name__ == "__main__":
    main()
