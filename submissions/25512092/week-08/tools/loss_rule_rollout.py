"""Person's decision 11/12 ~14:40, one seller restart for all:
  1. A / B grade high-price units (not sofas) over 24 h: floor max(cost x 1.00, 48 h median x 0.70) (high_ask.json age_ab)
  2. aging stops at cost; below cost only by the stoploss (loss_rule.json; the overstock 0.95 variant never under cost)
  3. stoploss share = days left x 1% (was 2%): cost at most that above the market average -> sold at the average
     (high price, sofas included); more above: never under cost; the endgame first
  4. a sofa floor above the average with cost above it: rule 3 too
Before: the units whose floor changes by 3 / 4 (and 1, 2) by Telegram; then both switches on, the seller restarted
(the kit starts it again), checked back. 3 h later one Telegram: the changed units sold (units, revenue, profit on
cost), 12 h+ unsold high-price trend, Book per hour after vs before.
    python tools/loss_rule_rollout.py [--preview | --report-dry]      (log in logs/loss_rule_rollout.log)
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402

LOG = os.path.join(HERE, "logs", "loss_rule_rollout.log")
STATE = os.path.join(HERE, "logs", "loss_rule_rollout.json")
LOSS = os.path.join(HERE, "loss_rule.json")
HIGH_ASK = os.path.join(HERE, "high_ask.json")
AGE_AB = {"enabled": True, "h": 24, "grades": ["A", "B"], "cost": 1.0, "median": 0.7}
LOSS_ON = {"enabled": True, "per_day": 0.01}
H = 3
ME = "choo-chu"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def targets(now=None, units=None, avg_of=None, case=None):
    """[{unit, sku, name, grade, kind, cost, avg, cur, new, rule}] for the held units whose floor changes; rule '1'
    (age_ab), '3/4' (to the average by the stoploss), '2' (aging stopped at cost)."""
    import pricing
    import strategy
    from depts import high_ask, sales, sofa
    from market_db import price_of
    now = now or time.time()
    case = case or strategy.current()[0]
    avg_of = avg_of or (lambda k: price_of(k)[0])
    if units is None:
        with open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
            units = [u for u in json.load(f).get("units", []) if u.get("grade") != "expired"]
    r0 = high_ask.rules()
    r1 = dict(r0, age_ab=AGE_AB)

    def fl(u, avg, rr, lr):
        with mock.patch.object(high_ask, "rules", lambda: rr), mock.patch.object(sales, "loss_rule", lambda: lr):
            res, rule = pricing.floor(u, avg, case, now)
            return high_ask.floor(u, avg, res, case, now, rule), rule
    out = []
    for u in units:
        avg = avg_of(u["sku"])
        f0, _ = fl(u, avg, r0, {})
        f1, _ = fl(u, avg, r1, {})
        f2, rule2 = fl(u, avg, r1, LOSS_ON)
        if f2 == f0:
            continue
        tag = "3/4" if f2 != f1 and rule2 == "stoploss" else "2" if f2 != f1 else "1"
        out.append({"unit": u["unit"], "sku": u["sku"], "name": (u.get("name") or "")[:16], "grade": u.get("grade"),
                    "sofa": bool(sofa.applies(u)), "cost": int(u["cost"]), "avg": avg, "cur": f0, "new": f2, "rule": tag,
                    "age_h": round((now - (u.get("supplied_at") or now)) / 3600)})
    return out


def preview_text(ts, now=None):
    from depts import sales
    share = sales.stoploss_share(now, LOSS_ON)
    L = [f"하한 규칙 적용 전 목록 (손절 기준 남은 일수 x 1% = {share:.1%})"]
    for tag, title in (("3/4", "3·4번 손절 -> 시장 평균"), ("1", "1번 A·B 24시간+"), ("2", "2번 aging 원가에서 멈춤 (하한 올라감)")):
        xs = sorted([t for t in ts if t["rule"] == tag], key=lambda t: -t["cost"])
        L.append(f"\n[{title}] {len(xs)}개, 원가 {sum(t['cost'] for t in xs):,}")
        for t in xs:
            L.append(f"{t['sku']} {t['name']}{' (소파)' if t['sofa'] else ''}: 원가 {t['cost']:,}, 평균 "
                     f"{int(t['avg'] or 0):,}, 원가/평균 {t['cost'] / t['avg']:.3f}, 하한 {int(t['cur']):,} -> {int(t['new']):,}"
                     if t["avg"] else f"{t['sku']}: 원가 {t['cost']:,}, 하한 {int(t['cur']):,} -> {int(t['new']):,}")
    return "\n".join(L)


def apply(now=None):
    now = now or time.time()
    with open(HIGH_ASK, encoding="utf-8") as f:
        ha = json.load(f)
    ha["age_ab"] = dict(AGE_AB, since=round(now), note="person 11/12 ~14:40: A / B high (not sofas) over 24 h: "
                        "max(cost, 48 h median x 0.7); lower floors kept")
    for path, obj in ((HIGH_ASK, ha), (LOSS, dict(LOSS_ON, since=round(now), why="person 11/12 ~14:40: below cost only by "
                      "the stoploss (days left x 1%, high and sofas too); aging stops at cost; overstock never under cost; "
                      "endgame first"))):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)


def report(s, now=None, db_path=None, inv_path=None, board=None):
    import board_read as BR
    import morning_summary as M
    now = now or time.time()
    t0 = s["t0"]
    h = max((now - t0) / 3600, 0.01)
    units = s["units"]
    with open(inv_path or os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        held = {u["unit"] for u in json.load(f).get("units", []) if u.get("grade") != "expired"}
    L = [f"하한 규칙 1~4 적용 {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} 적용, seller 재시작)"]
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        for tag, title in (("3/4", "3·4번 (손절 -> 평균)"), ("1", "1번 (A·B 24시간+)"), ("2", "2번 (aging 원가에서 멈춤)")):
            us = [u for u, t in units.items() if t == tag]
            if not us:
                L.append(f"- {title}: 대상 없음")
                continue
            q = ",".join("?" * len(us))
            n, rev, cost, loss = d.execute(
                f"SELECT COUNT(*), COALESCE(SUM(s.amount), 0), COALESCE(SUM(m.cost), 0), "
                f"COALESCE(SUM(MIN(0, s.amount - m.cost)), 0) FROM settles s JOIN my_stock m ON m.unit=s.unit "
                f"WHERE s.shop=? AND s.unit IN ({q}) AND s.ts>=?", (ME, *us, t0)).fetchone()
            sold = {x for (x,) in d.execute(f"SELECT unit FROM settles WHERE shop=? AND unit IN ({q})", (ME, *us))}
            left = len([x for x in us if x in held and x not in sold])
            L.append(f"- {title}: {len(us)}개 중 판매 {n}개, 매출 {rev:,}, 원가 {cost:,}, 손익 {rev - cost:+,}"
                     + (f", 실제 손실 합 {loss:,} (원가 아래로 판 건만), 남은 {left}개" if tag == "3/4" else ""))
    finally:
        d.close()
    tr = M.aged_high_trend(now, inv_path=inv_path, db_path=db_path, step_h=1, span_h=max(1, round(h)))
    L.append("- 12시간+ 미판매 고가: " + tr.split(": ", 1)[-1])
    cur = BR.now(board, db_path=db_path)
    pp, q0 = BR.at(t0 - h * 3600, db_path=db_path), BR.at(t0, db_path=db_path)
    bad = BR.failed(**{"직전 시작": pp, "적용 때": q0, "지금": cur})
    L.append(f"- 가게 시간당 Book {BR.show(BR.per_h(q0, cur, 's', 'book'), ',.0f')} vs 직전 같은 길이 "
             f"{BR.show(BR.per_h(pp, q0, 's', 'book'), ',.0f')}")
    if bad:
        L.append(BR.fail_line(bad))
    return "\n".join(L)


def main():
    if "--report-dry" in sys.argv:
        print(report(json.load(open(STATE, encoding="utf-8"))))
        return
    if "--report-only" in sys.argv:              # 11/12 ~15:05: restarted for the new 3/4 lines; rules already on
        s = json.load(open(STATE, encoding="utf-8"))
        log("restarted: report only")
        while time.time() < s["t0"] + H * 3600:
            time.sleep(60)
        try:
            alerts.send("daily", f"loss_rule_report:{s['t0']}", report(s), log)
            log("report sent")
        except Exception as e:
            alerts.send("urgent", "loss_rule_report_fail", f"하한 규칙 3시간 점검 실패: {type(e).__name__}: {e}", log)
        return
    ts = targets()
    text = preview_text(ts)
    if "--preview" in sys.argv:
        print(text)
        return
    alerts.send("daily", f"loss_rule_preview:{int(time.time())}", text, log)
    log(f"preview sent: {len(ts)} units")
    from seller_conc_rollout import restart_seller, seller_back
    t = time.time()
    apply(t)
    killed = restart_seller()
    log(f"rules on, seller restarted (killed {killed})")
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"t0": round(t), "units": {x["unit"]: x["rule"] for x in ts}}, f)
    time.sleep(120)
    back = seller_back(t)
    alerts.send("info" if back else "urgent", "loss_rule_applied",
                f"하한 규칙 1~4 적용 {datetime.fromtimestamp(t):%H:%M}: seller 재시작 " + (f"정상 ({back})" if back else
                                                                                    "후 확인 안 됨, 점검 필요"), log)
    while time.time() < t + H * 3600:
        time.sleep(60)
    try:
        alerts.send("daily", f"loss_rule_report:{round(t)}", report(json.load(open(STATE, encoding="utf-8"))), log)
        log("report sent")
    except Exception as e:
        alerts.send("urgent", "loss_rule_report_fail", f"하한 규칙 3시간 점검 실패: {type(e).__name__}: {e}", log)


if __name__ == "__main__":
    main()
