"""The status page (design/ideal-harness.md 17-6): what the seller, buyer and restock do and under which strategy,
the shop's stock and money, the buyer's deals and speed, the next restock, the market, and the record - the same
material the meta-harness judges from, for the user to see directly. One HTML file, no script (CSS tabs), phone first.

    py -m ops.status_page              # build once -> memory/facts/status_page.html, then upload (ops.cf_page.put)
    py -m ops.status_page --local      # build only
    py -m ops.status_page --loop       # every 10 min

Reads only local records; every section is built on its own so one broken source does not blank the page.
"""
import collections
import glob
import html
import json
import os
import statistics as st
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from core import atomic, paths  # noqa: E402

EVERY = 600
OUT = paths.memory() / "facts" / "status_page.html"


def e(x):
    return html.escape(str(x if x is not None else "-"))


def won(x):
    return "-" if x is None else f"{round(x):,}"


def hm(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def table(head, rows):
    h = "".join(f"<th>{e(c)}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class=tw><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>"


def jread(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jlines(p, since=0):
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("ts", 0) >= since:
                    out.append(d)
    except OSError:
        pass
    return out


def raw(source, kinds, since):
    rows = []
    for f in sorted(glob.glob(str(paths.memory() / "raw" / source / "*.jsonl")))[-2:]:
        for d in jlines(f, since):
            if d.get("kind") in kinds:
                rows.append(d)
    return rows


class Data:
    def __init__(self):
        self.now = time.time()
        self.cat = jread(HERE / ".market" / "catalog.json", {}) or {}
        self.inv = jread(HERE / ".market" / "inventory.json", {}) or {}
        self.pos = jread(paths.memory() / "facts" / "position.json", {}) or {}
        self.strategy = atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}
        self.buy_list = jread(paths.memory() / "facts" / "buy_list.json", {}) or {}
        self.health = jread(paths.memory() / "facts" / "health.json", {}) or {}
        self.bench = jread(paths.memory() / "facts" / "benchmark.json", {}) or {}
        self.cls = jread(paths.memory() / "harness" / "score_class.json", {}) or {}     # after the move (20-5)

    def name(self, sku, n=26):
        return e(((self.cat.get(sku) or {}).get("name") or sku)[:n])


def sec_class(d):
    """Design 17-6 tab 1 with the class view (harness.score_class), the new approvals, alerts and emergency stops."""
    from harness import alert, ops, wisdom
    c = d.cls
    rows = []
    for side, label in (("shop", "가게"), ("buyer", "buyer")):
        for f, x in ((c.get(side) or {}).get("parts") or {}).items():
            rows.append([label, e(f), e(x.get("rows_above", 0) + 1) + "/" + e((c.get(side) or {}).get("ranked")),
                         e(x.get("percentile")), e(round(x["weight"] * (1 - x["percentile"]), 3)), e(x.get("next"))])
    pend = wisdom.status()
    emerg = ops.emergency()
    al = [a for a in alert.recent(24, d.now) if a["level"] in ("now", "warn")][-8:][::-1]
    return (f"<div class=kpi><div><b>{e(c.get('rank'))}</b>/{e(c.get('students'))}<small>Class view 순위 (학생)</small></div>"
            f"<div><b>{e(c.get('percentile'))}</b><small>백분위</small></div>"
            f"<div><b>{e(c.get('total'))}</b><small>학생 합계 (가게 {e((c.get('board') or {}).get('shop'))} + buyer "
            f"{e((c.get('board') or {}).get('buyer'))})</small></div>"
            f"<div><b>{e(c.get('gap_next'))}</b><small>위 {e(c.get('next'))}까지 · 3위권까지 {e(c.get('gap_top3'))}</small></div></div>"
            + table(["쪽", "부분", "학생 순위", "백분위", "남은 여지", "바로 위"], rows)
            + ("<h3>비상 동작 중</h3><ul>" + "".join(f"<li>{e(k)}: {e(v.get('why'))} ({e(hm(v.get('since')))})</li>"
                                                for k, v in emerg.items()) + "</ul>" if emerg else "")
            + "<h3>승인 대기</h3>" + ("".join(f"<pre class=mono>{e(wisdom.render(x))}</pre>" for x in pend) or "<p>없음</p>")
            + "<h3>최근 알림 (24시간)</h3>" + ("<ul>" + "".join(
                f"<li>{e(hm(a['ts']))} [{e(a['level'])}{'' if a['sent'] else ', 휴대폰 안 감'}] {e(a['text'][:160])}</li>"
                for a in al) + "</ul>" if al else "<p>없음</p>"))


def sec_summary(d):
    if d.cls:
        return sec_class(d)
    p, s, b = d.pos.get("players") or {}, d.pos.get("shop") or {}, d.pos.get("buyer") or {}
    rows = []
    for side, label in ((s, "가게"), (b, "buyer")):
        for f, x in (side.get("parts") or {}).items():
            rows.append([label, e(f), e(x.get("rank")) + "/" + e(x.get("of")), e(round(x.get("percentile") or 0, 3)),
                         e(x.get("next")), won(x.get("gap")) if isinstance(x.get("gap"), (int, float)) and abs(x["gap"]) > 100
                         else e(x.get("gap"))])
    urgent = (HERE / "logs" / "strategy" / "URGENT.md")
    alerts = [l[2:] for l in urgent.read_text(encoding="utf-8").splitlines() if l.startswith("- ")][:6] if urgent.exists() else []
    pending = (HERE / "logs" / "strategy" / "PENDING.md")
    pend = [l for l in pending.read_text(encoding="utf-8").splitlines() if l.strip().startswith(("-", "|", "#"))][:8] \
        if pending.exists() else []
    return (f"<div class=kpi><div><b>{e(p.get('rank'))}</b>/{e(p.get('of'))}<small>Players 순위</small></div>"
            f"<div><b>{e(round(p.get('percentile') or 0, 3))}</b><small>백분위</small></div>"
            f"<div><b>{e(p.get('value'))}</b><small>합계 (가게 {e(p.get('shop_all'))} + buyer {e(p.get('buyer_all'))})</small></div>"
            f"<div><b>{e(p.get('gap'))}</b><small>위 계정 {e(p.get('next'))}까지</small></div></div>"
            "<p class=note>순위는 Players 표 전체 기준(외부 참가자 포함). Class view(학생끼리) 순위는 score.class 도구(설계 16장) 구현 후.</p>"
            + table(["쪽", "부분", "순위", "백분위", "바로 위", "거리"], rows)
            + "<h3>승인 대기</h3>" + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in pend) + "</ul>" if pend else "<p>없음</p>")
            + "<h3>최근 알림</h3><ul>" + "".join(f"<li>{e(a)}</li>" for a in alerts) + "</ul>")


def sec_seller(d):
    pb = (d.strategy.get("playbook") or {}).get("seller") or {}
    units = [u for u in d.inv.get("units", []) if u.get("grade") != "expired"]
    by = collections.defaultdict(list)
    for u in units:
        by[u["sku"]].append(u)
    rows = []
    for sku, us in sorted(by.items(), key=lambda kv: -sum(u["cost"] for u in kv[1])):
        c = d.cat.get(sku) or {}
        sh = c.get("shelf_hours")
        left = sorted(round(((u.get("expires_at") or 0) - d.now) / 3600) for u in us if u.get("expires_at"))
        age = max((d.now - (u.get("supplied_at") or d.now)) / 3600 for u in us)
        grades = collections.Counter(u.get("grade") for u in us)
        rows.append([d.name(sku), e(c.get("kind")), e(len(us)), e(" ".join(f"{g}{n}" for g, n in sorted(grades.items()))),
                     e(f"{left[0]}~{left[-1]}h / {sh}h" if left else "-"), won(sum(u['cost'] for u in us)),
                     won(c.get("list_price")), e(f"{age:.0f}h")])
    bal = d.inv.get("balance")
    stock = sum(u["cost"] for u in units)
    goods = sum(u["cost"] for u in units if u.get("kind") == "goods")
    cash = None if bal is None else bal - (d.inv.get("start") or 0) - (d.inv.get("injected") or 0)
    reserve = (d.buy_list.get("budget") or {}).get("reserve")
    sold = raw("seller", {"seller.closed"}, d.now - 2 * 3600)
    done = [x for x in sold if (x.get("data") or {}).get("outcome") == "completed"]
    recent = [[e(hm(x["ts"])[6:]), d.name(x["data"].get("sku")), won(x["data"].get("price")), won(x["data"].get("cost")),
               e(x["data"].get("claim"))] for x in done[-25:][::-1]]
    margin = sum((x["data"].get("price") or 0) - (x["data"].get("cost") or 0) for x in done)
    rev = sum(x["data"].get("price") or 0 for x in done)
    hs = d.health.get("seller") or {}
    return (f"<p>전략 v{e(d.strategy.get('version'))} · 시작가 정가의 {e(pb.get('open_share'))}, 최소 마진 {e(pb.get('min_margin'))}, "
            f"정리 판매 {e(pb.get('clearance_hours'))}h · 식품: 밝힌 시간에 맞춤 {e(pb.get('food_fit'))}, 모르면 가장 신선한 것 "
            f"{e(pb.get('food_fresh_unknown'))}</p>"
            f"<div class=kpi><div><b>{won(bal)}</b><small>잔고</small></div><div><b>{won(cash)}</b><small>현금(cash)</small></div>"
            f"<div><b>{won((cash or 0) + stock)}</b><small>book = 현금 + 재고 원가</small></div>"
            f"<div><b>{won(stock)}</b><small>재고 원가 ({len(units)}개)</small></div>"
            f"<div><b>{won(reserve)}</b><small>보증 적립금</small></div><div><b>{won(goods * 0.01)}</b><small>보관비/일 (goods 1%)</small></div></div>"
            f"<h3>최근 2시간 판매</h3><p>{len(done)}건, 매출 {won(rev)}, 마진 {won(margin)} · 30분 방문 {e(hs.get('visits'))}, "
            f"재고 없음 {e(hs.get('no_stock_share'))}</p>"
            + table(["시각", "상품", "판매가", "원가", "등급"], recent)
            + "<h3>재고 (원가 큰 순)</h3>" + table(["상품", "종류", "수량", "등급", "남은 유통기한", "원가 합", "정가", "가장 오래"], rows))


def sec_buyer(d):
    b = d.strategy.get("buyer") or {}
    pb = (d.strategy.get("playbook") or {}).get("buyer") or {}
    ev = raw("buyer", {"buyer.order", "buyer.closed"}, d.now - 3 * 3600)
    start, kind, last = {}, {}, {}
    for x in ev:
        o = (x.get("keys") or {}).get("order")
        if x["kind"] == "buyer.order":
            start[o], kind[o] = x["ts"], (x.get("data") or {}).get("kind")
        else:
            last[o] = x
    pay = {}
    for p in jlines(HERE / ".market" / "payments.jsonl"):
        pay[p.get("order")] = p
    rows, secs = [], []
    for o, x in sorted(last.items(), key=lambda kv: -kv[1]["ts"])[:30]:
        dd = x.get("data") or {}
        t = x["ts"] - start.get(o, x["ts"])
        secs.append(t)
        p = pay.get(o) or {}
        lp, paid = dd.get("list_price"), dd.get("receipt_price")
        status = "결제" if p.get("paid") else ("거절: " + str(p.get("reason"))[:30] if p else ("-" if dd.get("verdict") != "fair" else "대기"))
        rows.append([e(hm(x["ts"])[6:]), e(kind.get(o)), e((x.get("keys") or {}).get("counterpart")), won(lp), won(paid),
                     won((lp - paid) if (lp and paid) else None), e(f"{t:.1f}s"), e(status if dd.get("verdict") == "fair" else dd.get("verdict", "")[:28])])
    orders = [o for o in start]
    fair = [o for o, x in last.items() if (x.get("data") or {}).get("verdict") == "fair"]
    paid = [o for o in fair if (pay.get(o) or {}).get("paid")]
    med = st.median([last[o]["ts"] - start[o] for o in last if o in start]) if any(o in start for o in last) else None
    return (f"<p>전략: 식품 시작가 {e(b.get('food_open_share'))}, 목표 {e(b.get('food_target_share'))}, 상한 {e(pb.get('ceiling_share'))} · "
            f"식품 주문당 {e(pb.get('food_time_budget'))}초, 가게 {e(pb.get('max_shops_food'))}곳 · 유통기한 가드 {e(pb.get('shelf_guard'))}</p>"
            f"<div class=kpi><div><b>{len(orders)}</b><small>3시간 주문</small></div><div><b>{len(fair)}</b><small>정상 영수증</small></div>"
            f"<div><b>{len(paid)}</b><small>결제됨</small></div><div><b>{e(f'{med:.1f}s' if med else '-')}</b><small>주문 처리 시간 중앙값</small></div></div>"
            "<h3>최근 주문 (마지막 가게와의 결과)</h3>"
            + table(["시각", "종류", "상대 가게", "정가", "합의가", "잉여", "걸린 시간", "결과"], rows))


def sec_restock(d):
    r = d.inv.get("restock") or {}
    nxt = r.get("opens_at")
    lines = [[d.name(x["sku"]), e(x.get("target")), e(x.get("have")), e(x.get("buy")), e((x.get("why") or "")[:34]),
              e(x.get("short") or "")] for x in (d.buy_list.get("lines") or []) if x.get("target")][:30]
    surge = [[d.name(s.get("sku")), e(s.get("units")), e(s.get("factor"))] for s in (d.inv.get("surge") or [])[:12]]
    sup = []
    log = HERE / "logs" / "market-kit.log"
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            fh.seek(max(0, log.stat().st_size - 400_000))
            sup = [l.strip() for l in fh.readlines() if "[supply]" in l][-15:][::-1]
    except OSError:
        pass
    bud = d.buy_list.get("budget") or {}
    pb = (d.strategy.get("playbook") or {}).get("restock") or {}
    return (f"<p>다음 공급 창: <b>{e(time.strftime('%H:%M', time.localtime(nxt)) if nxt else '-')}</b> "
            f"{'(열림)' if r.get('open') else ''} · 계획 금액 {won(bud.get('planned'))}, 쓸 수 있는 현금 {won(bud.get('cash'))}, "
            f"재고 상한까지 {won(bud.get('stock'))} · 식품: 판매 {e(pb.get('food_windows'))}창 분량, 신선 기준 {e(pb.get('food_fresh_share'))}</p>"
            "<h3>다음 창에 살 목록</h3>" + table(["상품", "목표", "보유", "살 수량", "이유", "부족"], lines)
            + "<h3>원가 급등 (시장 1시간 구매량)</h3>" + table(["상품", "개수", "배율"], surge)
            + "<h3>최근 구매</h3><ul class=mono>" + "".join(f"<li>{e(s[:110])}</li>" for s in sup) + "</ul>")


def sec_market(d):
    sk = d.bench.get("skus") or {}
    lack = [[d.name(s), e(n), won(lp)] for s, n, lp in (sk.get("their_best_we_lack") or [])[:12]]
    def rows_of(group):                          # benchmark.json: {"ours": {...}, "top": [{...}, ...]}
        g = d.bench.get(group) or {}
        return ([g["ours"]] if isinstance(g.get("ours"), dict) else []) + [v for v in (g.get("top") or []) if isinstance(v, dict)]
    srow = [[e(v.get("name")), e(v.get("deals_per_h")), e(v.get("paid_share")), won(v.get("avg_paid")), e(v.get("review_stars"))]
            for v in rows_of("shops")][:6]
    brow = [[e(v.get("name")), e(v.get("deals_per_h")), e(v.get("goods_share")), won(v.get("surplus_per_deal"))]
            for v in rows_of("buyers")][:6]
    forum = jread(HERE / ".market" / "forum.json", []) or []
    odd = [p for p in forum if any(w in (p.get("text") or "") for w in ("MARKET NOTICE", "market operator", "5 stars", "SYSTEM"))]
    miss = [[d.name(s), e(n)] for s, n in ((d.health.get("seller") or {}).get("missing_skus") or [])[:10]]
    return ("<h3>상위 가게가 많이 팔고 우리가 없는 것 (6h)</h3>" + table(["상품", "판매 수", "정가"], lack)
            + "<h3>우리 가게에서 찾았지만 없던 것 (30분)</h3>" + table(["상품", "요청 수"], miss)
            + "<h3>벤치마크: 가게</h3>" + table(["가게", "시간당 거래", "정가 대비", "평균 판매가", "별점"], srow)
            + "<h3>벤치마크: buyer</h3>" + table(["buyer", "시간당 거래", "goods 비중", "거래당 잉여"], brow)
            + "<h3>특이 행동: 주입 시도로 보이는 글</h3><ul>"
            + "".join(f"<li>{e(p.get('author'))}: {e((p.get('text') or '')[:120])}</li>" for p in odd[:5]) + "</ul>"
            + "<p class=note>벤치마크의 해석은 가설(설계 R0). 학생만의 비교와 순위 급변 감지는 구현 예정.</p>")


def sec_record(d):
    hist = []
    for f in sorted(glob.glob(str(paths.memory() / "strategy" / "history" / "v*.json")),
                    key=lambda p: int(Path(p).stem[1:]) if Path(p).stem[1:].isdigit() else 0)[-10:][::-1]:
        s = jread(f, {}) or {}
        hist.append([e(s.get("version")), e((s.get("approved") or "")[:90])])
    dec = jlines(HERE / "logs" / "strategy" / "decisions.jsonl")[-6:][::-1]
    drow = [[e(hm(x.get("ts"))), e(x.get("proposal_id")), e(x.get("choice")), e((x.get("note") or "")[:60])] for x in dec]
    models = (d.health.get("models") or {})
    mrow = [[e(k), e(json.dumps(v, ensure_ascii=False)[:90])] for k, v in list(models.items())[:10]] if isinstance(models, dict) else []
    new = jlines(paths.memory() / "harness" / "decisions.jsonl")[-8:][::-1]       # the approval path after the move
    drow = [[e(hm(x.get("ts"))), e(x.get("id")), e(x.get("event") if x.get("event") == "request" else x.get("choice")),
             e((x.get("title") or x.get("note") or x.get("installed") or x.get("experiment") or "")[:60])]
            for x in new] + drow
    try:
        from harness import experiment, ops
        exps = [[e(x["id"]), e(x["metric"]), e(x["status"]), e(json.dumps(experiment.result(x["id"], d.now),
                                                                         ensure_ascii=False)[:120])]
                for x in experiment.ledger()["experiments"].values()][-6:]
        use = ops.model_usage(d.now)
        mrow = [[e(k), e(f"{v['calls']}회 / 예산 {v['budget']}")] for k, v in use.items()] or mrow
    except Exception:
        exps = []
    return ("<h3>실험</h3>" + (table(["실험", "지표", "상태", "결과 (코드 판정)"], exps) if exps else "<p>없음</p>")
            + "<h3>전략 버전 (최근 10)</h3>" + table(["판", "승인"], hist)
            + "<h3>결정 기록</h3>" + table(["시각", "브리프", "선택", "메모"], drow)
            + "<h3>모델 사용 (최근 1시간)</h3>" + table(["자리", "내용"], mrow))


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6b6b6b;--line:#e3e3e3;--acc:#3b4ba8;--card:#f6f6f8}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececec;--mut:#9a9a9a;--line:#2c2c30;--acc:#8d9cf0;--card:#1d1d21}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Malgun Gothic",sans-serif}
header{padding:12px 16px;border-bottom:1px solid var(--line)} header h1{font-size:17px;margin:0} header p{margin:2px 0 0;color:var(--mut);font-size:12px}
.tabs{display:flex;flex-wrap:wrap} .tabs>input{display:none}
.tabs>label{padding:9px 12px;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;font-weight:600}
.tabs>.panel{display:none;order:99;width:100%;padding:8px 16px 40px;box-sizing:border-box}
.tabs>input:checked+label{color:var(--acc);border-bottom-color:var(--acc)} .tabs>input:checked+label+.panel{display:block}
h3{font-size:14px;margin:18px 0 6px} .note{color:var(--mut);font-size:12px}
.kpi{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin:8px 0}
.kpi>div{background:var(--card);border-radius:8px;padding:8px 10px} .kpi b{font-size:17px;display:block} .kpi small{color:var(--mut);font-size:11px}
.tw{overflow-x:auto} table{border-collapse:collapse;font-size:12px;min-width:100%} th,td{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;white-space:nowrap}
th{color:var(--mut);font-weight:600} ul{padding-left:18px} li{margin:2px 0} .mono{font-family:ui-monospace,Consolas,monospace;font-size:11px}
"""


def build():
    d = Data()
    from ops import page_sections as ps          # user 11/6 13:xx: one page for everything, the strategy visible
    wrap = lambda fn: (lambda d: f"<section class=card>{fn(d)}</section>")   # noqa: E731  (the older tabs in a card)
    secs = [("홈", ps.sec_home), ("전략", ps.sec_strategy), ("메타", ps.sec_meta), ("seller", wrap(sec_seller)),
            ("buyer", wrap(sec_buyer)), ("restock", wrap(sec_restock)),
            ("시장", lambda d: ps.sec_market2(d) + wrap(sec_market)(d)), ("지식", ps.sec_knowledge),
            ("기록", wrap(sec_record))]
    tabs = []
    for i, (label, fn) in enumerate(secs):
        try:
            body = fn(d)
        except Exception as ex:                  # one broken source must not blank the page
            body = f"<p>이 탭을 만들지 못함: {e(type(ex).__name__)}: {e(ex)}</p>"
        tabs.append(f"<input type=radio name=t id=t{i}{' checked' if i == 0 else ''}><label for=t{i}>{e(label)}</label>"
                    f"<div class=panel>{body}</div>")
    return ("<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=robots content='noindex,nofollow'><title>R3 현황</title>"
            f"<style>{ps.CSS}.note{{color:var(--mut);font-size:12px}}</style></head><body><header><h1>gom31 Round 3 현황</h1>"
            f"<p>갱신 {e(time.strftime('%Y-%m-%d %H:%M:%S'))} · 10분마다 · 전략 v{e(d.strategy.get('version'))}</p></header>"
            f"<div class=tabs>{''.join(tabs)}</div></body></html>")


def once(upload=True):
    page = build()
    atomic.write_text(OUT, page) if hasattr(atomic, "write_text") else OUT.write_text(page, encoding="utf-8")
    if upload:
        from ops import cf_page
        cf_page.put(page)
    return len(page)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            try:
                print(time.strftime("%H:%M:%S"), "status page", once(), flush=True)
            except (Exception, SystemExit) as ex:
                print(time.strftime("%H:%M:%S"), "status page failed:", ex, flush=True)
            time.sleep(EVERY)
    else:
        print("built", once(upload="--local" not in sys.argv), "chars")
