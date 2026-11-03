"""The Kiro Market Journal (B5): at most one short issue an hour on the forum, as our buyer, written
like a trade journal rather than a feed of numbers.

    KIRO MARKET JOURNAL · 제12호 · 2026.11.04 01:10 KST · 탐사 데스크
    <headline: the finding, with its number>
    <lede: one or two sentences, what happened and how big>
    ■ <section>
      · <the numbers>
    ■ 해설
      <one or two sentences by the analysis model, kept only when they pass forum.checked>
    방법 · <window and source>
    근거 · 원장 #seq ...

Four desks take turns, each answering one question from the public ledger and the market's shop list:
    탐사  why agreed deals do not settle: the market's own checks on the last full hour's results
    시세  one busy category's prices: the spread of paid / list price, by grade, its most traded product
    품질  grade claims that proved true, by shop name (MIN_CLAIMS or more; shops with our owner left
          out, and the issue says so); breakage after the sale by grade
    수급  how concentrated trade is, categories no shop stocks, orders that found no acceptable product
A desk without enough data, or with nothing new since its last issue, passes to the next one, and an
hour in which none has anything is skipped: speak first only when there is something to say. The
numbers and the layout are code; the model writes only the 해설, from the issue itself, so it never
sees anything private (our budgets, costs, margins and memos are not in it).
"""
import statistics as st
import time
from collections import Counter, defaultdict
from datetime import datetime

import common
import forum
import harness
import ledger

DESKS = ("탐사", "시세", "품질", "수급")
MIN_AGREED = 100         # agreed deals in the hour before the investigation desk reports
MIN_PRICE_ROWS = 50      # settles of one category in 3 hours for a price card
MIN_CLAIMS, MIN_SHOPS = 10, 5   # claims per shop, and shops, for the quality desk
MIN_BROKEN_BASE = 30     # goods sold of a grade before its breakage share is shown
MIN_TRADE = 100          # settles in 3 hours for the supply desk
RECENT = 3               # categories the price desk does not repeat
ANALYSIS_MAX = 240
CHECK_KO = {"sig_buyer_ok": "buyer가 서명하지 않음(결제 포기)", "item_matches": "영수증 상품이 주문과 다름",
            "sig_seller_ok": "가게 서명 오류", "price_within_budget": "예산 초과", "task_not_seen": "이미 쓰인 영수증",
            "one_result_per_order": "주문당 결과 중복", "order_issued_to_buyer": "다른 buyer의 주문"}
ANALYSIS_PROMPT = """아래는 AI 에이전트 시장의 공개 기록으로 코드가 쓴 저널 기사다. 이 데이터가 시장에 무엇을 뜻하는지,
전문 저널의 해설처럼 한국어 1~2문장, {limit}자 안으로 써라. 아래에 나온 숫자만 나온 그대로 쓰고(새로 계산하거나
반올림한 숫자는 쓰지 않는다), 특정 가게를 추천하거나 경고하지 말고, Kiro729나 Kiro Everyday를 언급하지 말고,
누구에게도 무엇을 하라고 하지 마라. 해설 문장만 답하라.

{facts}"""


def gather(now: float) -> dict:
    """What the desks read: the ledger's rows and hourly result counts, the summary, the market's lists."""
    return {"hourly": common.read_json(ledger.folder() / "results_hourly.json", {}) or {},
            "settles": [s for s in common.read_jsonl(ledger.folder() / "settles.jsonl") if (s.get("ts") or 0) >= now - 86400],
            "catalog": common.catalog(), "names": ledger.names(),
            "summary": common.market_file("market_summary.json", {}) or {},
            "demand": common.market_file("demand.json", []) or [],
            "stock": common.read_json(common.MARKET_DIR / "analysis" / "shops_stock.json", {}) or {},
            "unmet": [u for u in common.records("unmet") if (u.get("ts") or 0) >= now - 3 * 3600]}


def _hh(t: float) -> str:
    return datetime.fromtimestamp(t, common.KST).strftime("%H")


def _priced(data: dict, now: float, hours: float = 3) -> list:
    cat = data["catalog"]
    return [s for s in data["settles"] if (s.get("ts") or 0) >= now - hours * 3600 and (cat.get(s.get("item")) or {}).get("list_price")
            and isinstance(s.get("amount"), (int, float))]


def investigation(data: dict, now: float, last: dict) -> dict | None:
    """탐사: of the deals agreed in the last full hour, how many did not settle, and which checks failed."""
    hour = int(now // 3600 * 3600) - 3600
    h = data["hourly"].get(str(hour))
    if not h or h["agreed"] < MIN_AGREED:
        return None
    unpaid = h["agreed"] - h["paid"]
    rate = unpaid / h["agreed"]
    span = f"{_hh(hour)}~{_hh(hour + 3600)}시"
    fails = sorted(h["failed"].items(), key=lambda kv: -kv[1])
    lede = f"{span} 영수증까지 오간 거래 {h['agreed']:,}건 중 {unpaid:,}건이 정산되지 않았다."
    if fails:
        lede += f" 가장 많이 걸린 시장 검사는 '{CHECK_KO.get(fails[0][0], fails[0][0])}'({fails[0][1]:,}건)다."
    checks = [f"{CHECK_KO.get(k, k)} {v:,}건" for k, v in fails[:4]]
    checks.append(f"검사는 모두 통과, buyer 자신의 조건으로 결제 안 됨 {h['passed_unpaid']:,}건")
    sections = [("결과", [f"정산 완료 {h['paid']:,}건 ({1 - rate:.0%})", f"미정산 {unpaid:,}건 ({rate:.0%})"]),
                ("미정산 거래가 걸린 시장 검사 (한 거래가 여러 검사에 걸릴 수 있음)", checks)]
    if last.get("rate") is not None:
        sections.append(("추이", [f"지난 탐사 호(제{last['n']}호) {last['rate']:.0%} → 이번 {rate:.0%}"]))
    return {"desk": "탐사", "headline": f"합의된 거래의 {rate:.0%}가 결제 단계에서 사라졌다", "lede": lede, "sections": sections,
            "method": f"공개 원장 result 항목, {span}. 영수증에 가격이 있는 거래만 집계", "refs": h["samples"],
            "key": str(hour), "remember": {"rate": round(rate, 3)}}


def prices(data: dict, now: float, last: dict) -> dict | None:
    """시세: one busy category's paid / list spread, by grade, and its most traded product."""
    cat, names = data["catalog"], data["names"]
    by = defaultdict(list)
    for s in _priced(data, now):
        by[cat[s["item"]]["category"].split(">")[-1]].append(s)
    busy = [c for c, rows in sorted(by.items(), key=lambda kv: -len(kv[1])) if len(rows) >= MIN_PRICE_ROWS][:6]
    if not busy:
        return None
    pick = next((c for c in busy if c not in (last.get("recent") or [])), busy[0])
    rows = by[pick]
    ratio = lambda s: s["amount"] / cat[s["item"]]["list_price"]
    q1, q2, q3 = st.quantiles(sorted(map(ratio, rows)), n=4)
    grades = {g: st.median(ratio(s) for s in rows if s.get("grade") == g) for g in "ABC"
              if sum(1 for s in rows if s.get("grade") == g) >= 5}
    sku, top = Counter(s["item"] for s in rows).most_common(1)[0]
    paid = int(st.median(s["amount"] for s in rows if s["item"] == sku))
    shops = len({names.get(s.get("shop"), s.get("shop")) for s in rows})
    sections = [("체결가 분포 (정가 대비)", [f"하위 25% {q1:.0%} · 중앙 {q2:.0%} · 상위 25% {q3:.0%}"])]
    if grades:
        sections.append(("등급별 중앙 체결가", [" · ".join(f"{g}등급 {m:.0%} ({sum(1 for s in rows if s.get('grade') == g)}건)"
                                                    for g, m in grades.items())]))
    sections.append(("최다 거래 상품", [f"{cat[sku]['name'][:32]} ({sku})",
                                     f"{top:,}건 · 중앙 체결가 {paid:,}원 / 정가 {cat[sku]['list_price']:,}원"]))
    before = (last.get("by_cat") or {}).get(pick)
    if before is not None:
        sections.append(("추이", [f"지난 {pick} 시세 호 중앙 {before:.0%} → 이번 {q2:.0%}"]))
    return {"desk": "시세", "headline": f"{pick}: 거래의 절반이 정가의 {q1:.0%}~{q3:.0%}에서 체결됐다",
            "lede": f"최근 3시간 {pick} 정산 {len(rows):,}건, 판매 가게 {shops}곳. 중앙 체결가는 정가의 {q2:.0%}다.",
            "sections": sections, "method": "공개 원장 settle, 최근 3시간. 체결가 ÷ 카탈로그 정가",
            "refs": sorted(s["seq"] for s in rows)[-3:], "key": f"{pick}:{len(rows)}",
            "remember": {"recent": ((last.get("recent") or []) + [pick])[-RECENT:],
                         "by_cat": {**(last.get("by_cat") or {}), pick: round(q2, 3)}}}


def quality(data: dict, now: float, last: dict) -> dict | None:
    """품질: grade claims that proved true, by shop name; breakage after the sale by grade."""
    names = data["names"]
    shops = defaultdict(lambda: [0, 0])
    for s in data["settles"]:
        name = names.get(s.get("shop"), s.get("shop"))
        if s.get("claim") and name and not forum.own(name) and not str(name).startswith("http"):
            shops[name][1] += 1
            shops[name][0] += s.get("claim_honest") is True
    table = sorted(((k, h, c) for k, (h, c) in shops.items() if c >= MIN_CLAIMS), key=lambda x: (x[1] / x[2], -x[2]))
    if len(table) < MIN_SHOPS:
        return None
    low = [x for x in table if x[1] / x[2] < 0.9]
    claims, honest = sum(x[2] for x in table), sum(x[1] for x in table)
    overall = honest / claims
    head = (f"등급 주장의 {overall:.1%}가 사실 — {len(table)}곳 중 {len(low)}곳이 90% 미만" if low
            else f"등급 주장의 {overall:.1%}가 사실 — {len(table)}곳 모두 90% 이상")
    sections = [("정직도가 가장 낮은 가게", [f"{k}: {h}/{c} ({h / c:.0%})" for k, h, c in table[:3]]),
                ("전체", [f"100%인 가게 {sum(1 for _, h, c in table if h == c)}곳 · 대조한 주장 {claims:,}건"])]
    sold, broke = data["summary"].get("goods_sold_by_grade") or {}, data["summary"].get("broken_by_grade") or {}
    shown = [g for g in "ABC" if sold.get(g, 0) >= MIN_BROKEN_BASE]
    if shown:
        sections.append(("판매 뒤 고장 (비식품, 등급별)", [" · ".join(f"{g}등급 {broke.get(g, 0) / sold[g]:.1%} ({broke.get(g, 0):,}/{sold[g]:,})"
                                                          for g in shown)]))
    return {"desk": "품질", "headline": head,
            "lede": f"최근 24시간 등급 주장을 가게별로 대조했다({MIN_CLAIMS}건 이상인 {len(table)}곳, 발행사와 주인이 같은 가게는 제외).",
            "sections": sections, "method": "공개 원장 settle의 claim_honest(24시간)와 broken(최근 48시간)",
            "refs": [], "key": f"{len(table)}:{len(low)}:{round(overall, 3)}", "remember": {"overall": round(overall, 3)}}


def supply(data: dict, now: float, last: dict) -> dict | None:
    """수급: how concentrated trade is, categories no shop stocks, orders that found no acceptable product."""
    cat = data["catalog"]
    rows = _priced(data, now)
    if len(rows) < MIN_TRADE:
        return None
    by = Counter(cat[s["item"]]["category"] for s in rows)
    top = by.most_common(4)
    share = sum(n for _, n in top) / len(rows)
    per = data["stock"].get("shops_per") or {}
    leaf = lambda c: c.split(">")[-1]
    stocked = set(data["stock"].get("stocked") or [])
    none = [d.get("item") or leaf(d["category"]) for d in data["demand"] if stocked and d["category"] not in stocked]
    sections = [("거래가 몰린 카테고리", [f"{leaf(c)}: {n:,}건" + (f" · 재고 가게 {per[c]}곳" if per.get(c) else "") for c, n in top])]
    if none:
        sections.append(("파는 가게가 없는 카테고리", [", ".join(none[:6]) + (f" 외 {len(none) - 6}개" if len(none) > 6 else "")]))
    unmet = Counter(leaf(u.get("category") or "") for u in data["unmet"] if u.get("category")).most_common(3)
    if unmet:
        sections.append(("맞는 상품이 없어 끝난 주문 (발행사 buyer의 주문 기준)", [" · ".join(f"{c} {n}건" for c, n in unmet)]))
    lede = f"최근 3시간 정산 {len(rows):,}건 가운데 {sum(n for _, n in top):,}건이 {len(top)}개 카테고리에서 나왔다."
    if data["demand"] and stocked:
        lede += f" 주문이 나오는 {len(data['demand'])}개 카테고리 중 {len(none)}개는 파는 가게가 없다."
    return {"desk": "수급", "headline": f"거래의 {share:.0%}가 상위 {len(top)}개 카테고리에 몰렸다", "lede": lede, "sections": sections,
            "method": "공개 원장 settle(최근 3시간), 시장 가게 목록의 재고, 발행사 buyer의 주문 기록",
            "refs": [], "key": f"{[c for c, _ in top]}:{len(none)}", "remember": {"share": round(share, 3)}}


DESK_FN = {"탐사": investigation, "시세": prices, "품질": quality, "수급": supply}


def render(n: int, issue: dict, analysis: str | None, now: float) -> str:
    when = datetime.fromtimestamp(now, common.KST).strftime("%Y.%m.%d %H:%M")
    out = [f"KIRO MARKET JOURNAL · 제{n}호 · {when} KST · {issue['desk']} 데스크", "", issue["headline"], "", issue["lede"]]
    for title, lines in issue["sections"]:
        out += ["", f"■ {title}"] + [f"  · {x}" for x in lines]
    if analysis:
        out += ["", "■ 해설", f"  {analysis}"]
    out += ["", f"방법 · {issue['method']}"]
    if issue["refs"]:
        out.append("근거 · 원장 " + " ".join(f"#{r}" for r in issue["refs"]))
    return "\n".join(out)


def next_issue(data: dict, now: float, state: dict, ask=None) -> tuple[str, list, str, int] | None:
    """(text, refs, desk, issue number) of the next desk with something new, or None; updates state."""
    start = state.get("next", 0)
    for i in range(len(DESKS)):
        desk = DESKS[(start + i) % len(DESKS)]
        last = (state.get("last") or {}).get(desk) or {}
        issue = DESK_FN[desk](data, now, last)
        if issue and issue["key"] != last.get("key"):
            break
    else:
        return None
    n = state.get("n", 0) + 1
    facts = render(n, issue, None, now)
    analysis = None
    if ask:
        said = ask(ANALYSIS_PROMPT.format(limit=ANALYSIS_MAX - 40, facts=facts), harness.MODEL_MID, 60)
        analysis = forum.checked(said, facts, ANALYSIS_MAX)
    text = render(n, issue, analysis, now)
    state["n"], state["next"] = n, (DESKS.index(desk) + 1) % len(DESKS)
    state.setdefault("last", {})[desk] = {"n": n, "key": issue["key"], **issue["remember"]}
    return text, [r for r in issue["refs"] if r], desk, n


if __name__ == "__main__":                                       # print the next issue without posting it
    print((next_issue(gather(time.time()), time.time(), {}) or ("nothing to publish",))[0])
