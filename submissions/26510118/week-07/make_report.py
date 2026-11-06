"""Week 07 -- the numbers in REPORT.md, generated from the committed logs.

    python make_report.py --snapshot   # save the dashboard's numbers for this account (network, once)
    python make_report.py              # rewrite every generated table in REPORT.md

Each table between <!-- gen:NAME --> and <!-- /gen --> in REPORT.md is
rebuilt from the files below; everything outside the markers is prose and is
left exactly as it is. A number in the report cannot disagree with the run it
describes, and rerunning this after another market session updates them all.

    logs/market-kit.log                 the kit's record of every session (buyer, shop, signing)
    logs/challenge-1/ledger-mine.json   this shop's rows of the public ledger (reprice.py)
    logs/dashboard-snapshot.json        the dashboard and the Cloudflare buyer's totals, saved by --snapshot
    logs/cloudflare-buyer.json          the Cloudflare buyer's status and recent order logs, same moment
    asks.json, test-report.json
"""
import argparse
import json
import re
import statistics
from datetime import datetime
from pathlib import Path

import market_buyer as mb
import reprice

HERE = Path(__file__).resolve().parent
REPORT = HERE / "REPORT.md"
KIT_LOG = HERE / "logs" / "market-kit.log"
SNAPSHOT = HERE / "logs" / "dashboard-snapshot.json"
NAME = "Kiro729"
CLOUDFLARE = "https://kiro-shop.kiro729.workers.dev"   # the deployed shop and buyer (worker/)

LEDGER = re.compile(r"\[market\] ledger #(\d+): (?:(\w+) at (\d+)|no deal), stars \S+, verified (True|False)")
JOURNAL = re.compile(r"\[seller\] journal: (\w+) (deal|rejected|walked)(?: at (\d+))? after (\d+) offer")
LIST = re.compile(r"\[seller\] list price (\w+): harness '\d+' -> (\d+)")


# ------------------------------------------------------------ the kit log

def sessions() -> list[dict]:
    """The kit log cut into join sessions, with each buyer order and its result."""
    out, cur, order = [], None, None
    for line in KIT_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        t = line[:8]
        if "[kit] market_kit" in line:
            cur = {"start": t, "last": t, "tunnel": None, "round": None, "left": False,
                   "orders": [], "lines": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        cur["lines"].append(line)
        cur["last"] = t
        if m := re.search(r"\[tunnel\] (https://[^\s,]+)", line):
            cur["tunnel"] = m[1]
        elif m := re.search(r"buyer registered as \S+, round (\d)", line):
            cur["round"] = int(m[1])
        elif "leaving the market" in line:
            cur["left"] = True
        elif m := mb._ORDER.search(line):
            order = {"item": m[1], "budget": int(m[2]), "round": int(m[3]), "result": None}
            cur["orders"].append(order)
        elif order and "printed no result line" in line:
            order["result"] = "missing"
        elif order and (m := LEDGER.search(line)):
            order.update(result="deal" if m[2] else "nodeal", seq=int(m[1]),
                         price=int(m[3]) if m[3] else None, verified=m[4] == "True")
    return out


def count(s: dict, pattern: str) -> int:
    return sum(1 for line in s["lines"] if re.search(pattern, line))


# ------------------------------------------------------------ the tables

def table(head: list, rows: list) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def pct(v, digits=1):
    return "-" if v is None else f"{v:.{digits}%}"


def f3(v):
    return "-" if v is None else f"{v:.3f}"


def g_sessions(S, snap):
    dates = snap.get("session_dates", [])
    rows = []
    for i, s in enumerate(S):
        end = f"{s['last']} Ctrl+C" if s["left"] else f"{s['last']} (종료 줄 없음)"
        where = (f"`{s['tunnel'].replace('https://', '')}`" if s["tunnel"]
                 else f"`{CLOUDFLARE.replace('https://', '')}` (--deployed)")
        rows.append([i + 1, dates[i] if i < len(dates) else "-", s["start"], end, s["round"], where,
                     len(s["orders"])])
    return table(["세션", "날짜", "시작", "끝", "등록 라운드", "가게 주소", "손님 주문"], rows)


def g_buyer(S, snap):
    rows = []
    for i, s in enumerate(S):
        for rnd in sorted({o["round"] for o in s["orders"]}):
            os_ = [o for o in s["orders"] if o["round"] == rnd]
            deals = [o for o in os_ if o["result"] == "deal"]
            paid = [o["price"] / o["budget"] for o in deals]
            rows.append([i + 1, rnd, len(os_), len(deals),
                         sum(1 for o in deals if o["verified"]),
                         sum(1 for o in os_ if o["result"] == "nodeal"),
                         sum(1 for o in os_ if o["result"] == "missing"),
                         f"{statistics.fmean(paid):.3f}" if paid else "-"])
    return table(["세션", "라운드", "주문", "거래", "서명 검증 통과", "거래 없음", "결과 줄 없음", "지불/예산"], rows)


def g_shop(S, snap):
    cache = json.loads(reprice.CACHE.read_text(encoding="utf-8"))
    changed = json.loads((HERE / "asks.json").read_text(encoding="utf-8"))["changed_at"]
    rows = [[f"{r['tag']} {r['from']}-{r['to']}", r["round"], r["visits"], r["deals"], pct(r["share"], 3),
             f3(r["price_reserve"]), f3(r["paid_budget"]),
             f"{r['stars']:.2f} ({r['reviews']})" if r["stars"] is not None else "-"]
            for r in reprice.report_rows(cache, changed)]
    return table(["구간", "라운드", "방문", "거래", "점유율", "판매가/하한가", "지불/예산", "별점 (후기 수)"], rows)


def g_dashboard(S, snap):
    if not snap:
        return "(python make_report.py --snapshot 을 먼저 실행한다)"
    rows = [[f"가게 Round {r}", x["deals"], x["visits"], pct(x["share"], 2),
             f"{x['stars']:.2f}" if x.get("stars") else "-", x["reviews"]]
            for r, x in sorted(snap["shop_rounds"].items())]
    b = snap["buyer"]
    out = table(["대시보드", "거래", "방문", "점유율", "별점", "후기"], rows)
    out += (f"\n\n손님 `{NAME}` (누적): 주문 {b['orders']}, 거래 {b['deals']}, "
            f"예산 대비 지불 {b['paid']:.3f}, 아낀 금액 {b['saved']}, 하한가 미만 예산(hopeless) {b['hopeless']}. "
            f"스냅숏 {snap['taken']}.")
    return out


def g_journal(S, snap):
    rows = []
    for i, s in enumerate(S):
        js = [m for m in (JOURNAL.search(line) for line in s["lines"]) if m]
        for item in ("lamp", "chair", "bookshelf"):
            mine = [m for m in js if m[1] == item]
            if not mine:
                continue
            deals = [m for m in mine if m[2] == "deal"]
            rows.append([i + 1, item, len(mine), len(deals),
                         sum(1 for m in mine if m[2] == "rejected"), sum(1 for m in mine if m[2] == "walked"),
                         f"{statistics.fmean(int(m[3]) for m in deals):.1f}" if deals else "-",
                         f"{statistics.fmean(int(m[4]) for m in deals):.1f}" if deals else "-"])
    return table(["세션", "품목", "흥정", "성사", "가게가 끝냄", "손님이 떠남", "평균 판매가", "평균 제안 횟수"], rows)


def g_signing(S, snap):
    rows = [[i + 1, count(s, r"\[sign\] receipt has no sig_seller"),
             count(s, r"\[sign\] sig_seller does not verify"),
             count(s, r"does not fit the order, not signing"),
             count(s, r"\[sign\] sig_seller ok, sig_buyer added")] for i, s in enumerate(S)]
    out = table(["세션", "가게 서명 없음", "가게 서명 불일치", "주문과 안 맞아 서명 거부", "검증 후 sig_buyer 추가"], rows)
    cache = json.loads(reprice.CACHE.read_text(encoding="utf-8"))
    mine = [r for r in cache["results"] if r["price"]]
    rows = [[rnd, sum(1 for r in mine if r["round"] == rnd),
             sum(1 for r in mine if r["round"] == rnd and r.get("verified"))] for rnd in (0, 1, 2)]
    return out + "\n\n" + table(["내 가게 영수증 (원장)", "거래", "서명 검증 통과"], rows)


def g_asks(S, snap):
    asks = json.loads((HERE / "asks.json").read_text(encoding="utf-8"))
    old: dict[str, list] = {}
    for s in S:
        for line in s["lines"]:
            if m := LIST.search(line):
                old.setdefault(m[1], []).append(m[2])
    rows = []
    for item, why in asks["why"].items():
        rows.append([item, why["reserve"], " / ".join(dict.fromkeys(old.get(item, ["-"]))), asks["asks"][item],
                     why["customers"], f"{why['before']['profit_per_sale']} / {why['before']['stars']}",
                     f"{why['after']['profit_per_sale']} / {why['after']['stars']}"])
    fit = asks["star_fit"]
    return (table(["품목", "하한가", "이전 호가 (하네스)", "새 호가", "재현한 손님",
                   "이전 호가 재현: 이익/별점", "새 호가 재현: 이익/별점"], rows)
            + f"\n\n별점 직선: stars = {fit['a']:.2f} + {fit['b']:.2f} x 할인율 (내 후기 {fit['reviews']}건), "
              f"흥정 후 지불 비율 중앙값 {asks['haggle']:.3f}.")


def g_ch2_incidents(S, snap):
    rows = []
    for s in S:
        cur = {}
        for i, line in enumerate(s["lines"]):
            if m := mb._ORDER.search(line):
                cur = {"t": line[:8], "item": m[1], "budget": m[2]}
            elif m := mb._SHOP.search(line):
                cur["shop"] = m[1]
            elif (m := mb._OVER.search(line)) and "receipt is wrong" in line:
                verdict = next((x for x in s["lines"][i + 1:i + 6] if "[market] ledger" in x), "")
                v = re.search(r"ledger #(\d+): .*verified (True|False)", verdict)
                rows.append([cur["t"], cur.get("shop"), cur["item"], cur["budget"], m[2], int(float(m[1])),
                             f"#{v[1]} {'집계됨' if v[2] == 'True' else '거부'}" if v else "-"])
    return table(["시각", "가게", "품목", "예산", "합의가", "영수증 가격", "원장"], rows)


def g_ch2_live(S, snap):
    """Per session, how the buyer's filter treated the two shops that cheated
    it in Round 0: avoided by memory, avoided by reviews, or left a candidate."""
    rows = []
    for i, s in enumerate(S):
        row = [i + 1, len(s["orders"])]
        for shop in ("elm-street", "maple-home"):
            row += [count(s, rf"{shop}: stars .*avoided: my memory"),
                    count(s, rf"{shop}: stars .*(avoided\]|avoided: reviews)"),
                    count(s, rf"{shop}: stars [^\[]*$")]
        rows.append(row + [count(s, r"chose (elm-street|maple-home) ")])
    return table(["세션", "손님 주문", "elm-street 기억으로 회피", "후기로 회피", "후보로 남음",
                  "maple-home 기억으로 회피", "후기로 회피", "후보로 남음", "둘 중 하나를 고름"], rows)


def g_cloudflare(S, snap):
    cf = snap.get("cloudflare_buyer")
    if not cf:
        return "(python make_report.py --snapshot 을 먼저 실행한다)"
    span = (f"{datetime.fromtimestamp(cf['since']).strftime('%m-%d %H:%M')} ~ "
            f"{datetime.fromtimestamp(cf['until']).strftime('%m-%d %H:%M')}")
    rows = [[span, cf["orders"], cf["deals"], cf["verified"], cf["feasible"],
             f"{cf['feasible_deals']} ({cf['feasible_deals'] / max(1, cf['feasible']):.0%})",
             cf["refused"], cf["broke_off"]]]
    return (table(["Cloudflare 구매자", "주문", "거래", "서명 검증 통과", "하한가 이상 예산", "그중 성사",
                   "협상 거부로 떠남", "흥정 끊김"], rows)
            + f"\n\n`logs/cloudflare-buyer.json`에 그 시점의 집계와 최근 주문 로그를 저장했다 (스냅숏 {snap['taken']}).")


def g_test(S, snap):
    r = json.loads((HERE / "test-report.json").read_text(encoding="utf-8"))
    ok = sum(1 for x in r["results"] if x["pass"])
    return (f"market test {ok}/{len(r['results'])} 통과 ({r['started']}, {r['harness']}). "
            f"카드 버전 {r['seller_card'].get('version')}.")


GEN = {"sessions": g_sessions, "buyer": g_buyer, "shop": g_shop, "dashboard": g_dashboard,
       "journal": g_journal, "signing": g_signing, "asks": g_asks,
       "ch2-incidents": g_ch2_incidents, "ch2-live": g_ch2_live, "cloudflare": g_cloudflare, "test": g_test}


# ------------------------------------------------------------ the snapshot

def snapshot(S) -> None:
    """The dashboard's rows for this account, and the date of each kit session
    (the kit log has times only; the ledger entry a session wrote has the date)."""
    import httpx
    with httpx.Client(timeout=30) as c:
        stats = c.get(f"{reprice.MARKET}/api/stats").json()
        board = c.get(f"{reprice.MARKET}/api/board").json()
        dates = []
        for s in S:
            seq = next((o["seq"] for o in s["orders"] if o.get("seq")), None)
            p = c.get(f"{reprice.MARKET}/api/ledger", params={"after": seq - 1}).json() if seq else []
            dates.append(datetime.fromtimestamp(p[0]["ts"]).strftime("%Y-%m-%d") if p else "-")
        cf_status = c.get(f"{CLOUDFLARE}/buyer/status").json()
        cf_log = c.get(f"{CLOUDFLARE}/buyer/log", params={"n": 10}).json()
    shop_rounds = {r: next((x for x in R["shops"] if x["shop"] == NAME), None)
                   for r, R in stats["rounds"].items()}
    buyer = next(x for x in board["buyers"] if x["name"] == NAME)
    SNAPSHOT.write_text(json.dumps({
        "taken": datetime.now().strftime("%Y-%m-%d %H:%M"), "session_dates": dates,
        "shop_rounds": {r: x for r, x in shop_rounds.items() if x},
        "buyer": {k: buyer[k] for k in ("orders", "deals", "paid", "saved", "hopeless")},
        "market": {r: {"deals": R["deals"], "items": R["items"]} for r, R in stats["rounds"].items()},
        "cloudflare_buyer": {k: v for k, v in cf_status.items() if k != "recent"},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "logs" / "cloudflare-buyer.json").write_text(json.dumps(
        {"status": cf_status, "recent_orders_with_log": cf_log}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {SNAPSHOT.relative_to(HERE)} and logs/cloudflare-buyer.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    a = ap.parse_args()
    S = sessions()
    if a.snapshot:
        snapshot(S)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    text = REPORT.read_text(encoding="utf-8")
    for name, fn in GEN.items():
        # Non-greedy up to the first end marker, so an empty block never swallows the next one.
        body = fn(S, snap)
        text, n = re.subn(rf"<!-- gen:{re.escape(name)} -->\n.*?<!-- /gen -->",
                          lambda m: f"<!-- gen:{name} -->\n{body}\n<!-- /gen -->", text, flags=re.DOTALL)
        if not n:
            print(f"(no <!-- gen:{name} --> block in REPORT.md)")
    REPORT.write_text(text, encoding="utf-8")
    print(f"wrote {REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
