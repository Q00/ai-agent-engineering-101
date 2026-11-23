"""Hourly sales and normal-goods stock (person's request 11/10 ~14:00, for the 15:00 check), read-only:
  - my sales per hour over the last H hours, high price (unit cost > qc.HIGH_MIN) / normal
  - normal goods (unit cost <= HIGH_MIN) held at each hour mark: units = the 5-min cash snapshot's units minus the
    high-price units held then; SKUs rebuilt from my_stock (supplied_at, first sale, food supplied_at + shelf_hours),
    leaving out units that left without a sale (their end is unknown), so past SKU counts are a lower bound
  - "not in stock" replies per hour (logs/subst_exp.jsonl offer / no_offer, the moment of the answer): not held
    (held 0) vs held but locked / in a receipt hold, normal vs high price (catalog list price x COST_SHARE)
  - a verdict: is the drop in sales explained by normal goods running out? -> request-based lines 2 -> 3-4 or not
    python tools/sales_trend.py [hours]
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
ME = "choo-chu"
DROP = 0.15                                   # a 15% fall counts as a drop


def _catalog():
    with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
        return json.load(f)


def _live_units(path=None):
    try:
        with open(path or os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
            return {u["unit"] for u in json.load(f).get("units", [])}
    except (OSError, ValueError, KeyError):
        return None


def hourly(now=None, h=12, db_path=None, subst_log=None, catalog=None, inv_path=None):
    """[{t, sales_high, sales_low, low_units, low_skus, ns_total, ns_none_low, ns_none_high, ns_lock}] one per hour,
    oldest first; t = the hour's start; stock at the hour's end."""
    from depts.qc import HIGH_MIN
    from depts.purchasing import COST_SHARE
    now = now or time.time()
    cat = catalog if catalog is not None else _catalog()
    t0 = now - h * 3600
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        sales = db.execute("SELECT s.ts, m.cost FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND "
                           "s.ts>=? AND s.ts<?", (ME, t0, now)).fetchall()
        life = db.execute("SELECT m.unit, m.sku, m.cost, m.supplied_at, (SELECT MIN(s.ts) FROM settles s WHERE "
                          "s.unit=m.unit AND s.shop=?) FROM my_stock m WHERE m.supplied_at<?", (ME, now)).fetchall()
        snaps = db.execute("SELECT ts, units FROM cash WHERE ts>=? AND ts<=? ORDER BY ts", (t0, now + 60)).fetchall()
    finally:
        db.close()
    live = _live_units(inv_path)
    span, hspan = [], []
    for unit, sku, cost, sup, sold in life:
        c = cat.get(sku) or {}
        if sold is None and live is not None and unit not in live:
            continue                     # left without a sale (expired / removed): its end time is unknown
        end = sold if sold is not None else float("inf")
        if c.get("kind") == "food" and c.get("shelf_hours"):
            end = min(end, (sup or 0) + c["shelf_hours"] * 3600)
        (hspan if (cost or 0) > HIGH_MIN else span).append((sku, sup or 0, end))

    def total_at(t):                     # the 5-min cash snapshot nearest t (all held units)
        best = min(snaps, key=lambda r: abs(r[0] - t)) if snaps else None
        return best[1] if best and abs(best[0] - t) <= 900 else None
    ns = []
    try:
        with open(subst_log or os.path.join(HERE, "logs", "subst_exp.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if '"offer"' not in ln and '"no_offer"' not in ln:
                    continue
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("event") in ("offer", "no_offer") and t0 <= e["ts"] < now:
                    ns.append(e)
    except OSError:
        pass

    def high(sku):
        c = cat.get(sku) or {}
        return (c.get("list_price") or 0) * COST_SHARE.get(c.get("kind", "goods"), 0.6) > HIGH_MIN

    out = []
    for i in range(h):
        a, b = t0 + i * 3600, t0 + (i + 1) * 3600
        held = [k for k, s, e in span if s <= b < e]
        tot = total_at(b)
        n_high = sum(1 for k, s, e in hspan if s <= b < e)
        hn = [e for e in ns if a <= e["ts"] < b]
        none = [e for e in hn if not (e.get("cause") or {}).get("held")]
        out.append({"t": a, "sales_high": sum(1 for ts, c in sales if a <= ts < b and (c or 0) > HIGH_MIN),
                    "sales_low": sum(1 for ts, c in sales if a <= ts < b and (c or 0) <= HIGH_MIN),
                    "low_units": tot - n_high if tot is not None else len(held), "low_skus": len(set(held)), "ns_total": len(hn),
                    "ns_none_low": sum(1 for e in none if not high(e.get("x"))),
                    "ns_none_high": sum(1 for e in none if high(e.get("x"))),
                    "ns_lock": sum(1 for e in hn if (e.get("cause") or {}).get("cause") in ("lock", "receipt"))})
    return out


def verdict(rows):
    """(text, propose) comparing the second half of the rows with the first. Buyers ask only for SKUs on my card, so a
    "not in stock" for a SKU not held is rare (almost all are locks): the test is how much of the normal-goods sales
    drop the stock drop explains (stock drop / sales drop, the larger of units and SKUs): >= 0.7 the main cause
    (request lines 2 -> 4), >= 0.3 a partial one (2 -> 3, 3 h look), else not the cause."""
    k = len(rows) // 2
    A, B = rows[:k], rows[k:]
    s = lambda R, f: sum(r[f] for r in R)
    avg = lambda R, f: s(R, f) / len(R) if R else 0
    low0, low1 = s(A, "sales_low"), s(B, "sales_low")
    hi0, hi1 = s(A, "sales_high"), s(B, "sales_high")
    u0, u1 = avg(A, "low_units"), avg(B, "low_units")
    k0, k1 = avg(A, "low_skus"), avg(B, "low_skus")
    n0, n1 = s(A, "ns_none_low"), s(B, "ns_none_low")
    l0, l1 = s(A, "ns_lock"), s(B, "ns_lock")
    ch = lambda x, y: (y - x) / x if x else 0.0
    sd, st = ch(low0, low1), min(ch(u0, u1), ch(k0, k1))
    head = (f"앞 {k}시간 vs 뒤 {len(B)}시간: 일반 판매 {low0} -> {low1} ({sd:+.0%}), 고가 판매 {hi0} -> {hi1} "
            f"({ch(hi0, hi1):+.0%}); 일반 재고 평균 {u0:.0f} -> {u1:.0f}개 ({ch(u0, u1):+.0%}), SKU {k0:.0f} -> {k1:.0f} "
            f"({ch(k0, k1):+.0%}); 재고 1개당 일반 판매 {low0 / u0 if u0 else 0:.2f} -> {low1 / u1 if u1 else 0:.2f}; "
            f"'재고 없음' 응답 미보유(일반) {n0} -> {n1}, 잠김·영수증 대기 {l0} -> {l1}")
    if sd > -DROP:
        return head + ". 판단: 일반 판매가 크게 줄지 않음 -> 요청 기반 2줄 그대로 제안", False
    share = st / sd if sd < 0 and st < 0 else 0.0
    if share >= 0.7:
        return (head + f". 판단: 일반 판매 감소의 대부분({share:.0%})이 일반 재고·SKU 감소로 설명됨 -> 일반 재고 부족이 주원인. "
                "요청 기반 매입 2줄 -> 4줄 복귀 제안 (사람 결정)"), True
    if share >= 0.3:
        return (head + f". 판단: 일반 재고 감소가 판매 감소의 일부({share:.0%})만 설명 -> 요청 기반 2줄 -> 3줄로 3시간 보기 제안 "
                "(사람 결정)"), True
    return head + ". 판단: 일반 재고는 그대로인데 판매만 줄었음 -> 재고 부족 아님, 2줄 그대로 제안", False


def text(now=None, h=12):
    rows = hourly(now, h)
    L = [f"최근 {h}시간 시간대별 (시각: 판매 고가/일반, 일반 재고 개수/SKU, '재고 없음' 응답 전체 [미보유 일반·고가 / 잠김]):"]
    for r in rows:
        L.append(f"  {datetime.fromtimestamp(r['t']):%H}시 판매 {r['sales_high']}/{r['sales_low']}, 일반 재고 {r['low_units']}/"
                 f"{r['low_skus']}, 재고 없음 {r['ns_total']} [{r['ns_none_low']}·{r['ns_none_high']} / {r['ns_lock']}]")
    L.append("- " + verdict(rows)[0])
    return "\n".join(L)


if __name__ == "__main__":
    print(text(h=int(sys.argv[1]) if len(sys.argv) > 1 else 12))


def request_lines_text():
    """'- 요청 기반 4줄 복귀 HH:MM (정식 3 + 시험 1)' from request_lines.json, or ''."""
    try:
        with open(os.path.join(HERE, "request_lines.json"), encoding="utf-8") as f:
            r = json.load(f)
        return (f"- 요청 기반 매입 {r['lines']}줄 복귀 {datetime.fromtimestamp(r['since']):%m-%d %H:%M} (정식 {r['regular']} + 새 SKU "
                f"시험 {r['trial']}, 회전 1시간 이하 목표 8개 그대로)")
    except (OSError, ValueError, KeyError):
        return ""


def recovery_line(now=None, h=12):
    """The 08:00 / 20:00 summary line: normal stock units / SKUs and normal sales per hour, hour by hour."""
    rows = hourly(now, h)
    return ("일반 상품 회복 추이 (시각 판매/h · 재고 개수/SKU): "
            + ", ".join(f"{datetime.fromtimestamp(r['t']):%H}시 {r['sales_low']}·{r['low_units']}/{r['low_skus']}" for r in rows)
            + (" | " + request_lines_text()[2:] if request_lines_text() else ""))
