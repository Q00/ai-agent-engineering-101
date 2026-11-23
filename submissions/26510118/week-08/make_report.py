"""REPORT.md part (3), Round 3 results, printed from the committed logs, so no number in it is typed by hand.

    python make_report.py            # rewrites the block between the two markers in REPORT.md
    python make_report.py --print    # prints the block only

Sources (all under logs/, copied unedited from the server; see logs/manual_runs.log):
    round3-final/2358/standing.txt, board.json   the board as served at 23:58:43 KST, 11/17 (the round ended at 23:59)
    round3-final/board_history.jsonl              our own board numbers every 20 min since 11/3 (analysis.py)
    market-kit.log.1, market-kit.log              the kit's log; it rotates at 20 MB and overwrites .1, so the two
                                                  files hold the last ~46 h of the round, not all of it
Every quoted line is read from its file here, with its line number.
"""
import calendar
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
FINAL = LOGS / "round3-final" / "2358"
KIT = ["market-kit.log.1", "market-kit.log"]           # oldest first
START, END = "<!-- make_report:part3 start -->", "<!-- make_report:part3 end -->"
ROUND_END = calendar.timegm((2026, 11, 17, 14, 59, 0))    # 11/17 23:59 KST in UTC, when the market stopped
WEEK10 = calendar.timegm((2026, 11, 10, 3, 0, 0))         # 11/10 12:00 KST, the week-10 LAB
KST = 9 * 3600
TIME = re.compile(r"^(\d\d):(\d\d):(\d\d) ")


def kst(ts: float, fmt: str = "%m/%d %H:%M") -> str:
    return time.strftime(fmt, time.gmtime(ts + KST))


def n(x) -> str:
    return f"{x:,}"


def dated_lines():
    """(file, line number, UTC timestamp, text) for every timed line of the kit logs. The lines carry a UTC time of
    day only (the server's clock); the date comes from the end: the "Round 3 ended at" errors came from 14:59 UTC
    on 11/17 until the kit was stopped at 18:13 UTC, and walking back, each step from early morning to late night
    is a day."""
    rows = []
    for name in KIT:
        with open(LOGS / name, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                m = TIME.match(line)
                if m:
                    rows.append((name, i, int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]), line.rstrip("\n")))
    anchor = next(k for k, r in enumerate(rows) if "Round 3 ended at" in r[3])
    base = calendar.timegm((2026, 11, 17, 0, 0, 0)) if rows[anchor][2] >= 12 * 3600 else calendar.timegm((2026, 11, 18, 0, 0, 0))
    day = [0] * len(rows)
    for k in range(anchor + 1, len(rows)):
        day[k] = day[k - 1] + (1 if rows[k][2] < rows[k - 1][2] - 12 * 3600 else 0)
    for k in range(anchor - 1, -1, -1):
        day[k] = day[k + 1] - (1 if rows[k][2] > rows[k + 1][2] + 12 * 3600 else 0)
    return [(f, i, base + day[k] * 86400 + s, text) for k, (f, i, s, text) in enumerate(rows)]


# What a line means: (key, regex). The first match wins.
SHOP = [("agreed", re.compile(r"\[seller\] \[seller\] G[0-9a-f]+ deal at \d+")),
        ("rejected", re.compile(r"\[seller\] \[seller\] G[0-9a-f]+ rejected$")),
        ("walked", re.compile(r"\[seller\] \[seller\] G[0-9a-f]+ walked$")),
        ("settled", re.compile(r"\[acp\] settled .* at \d+, ledger #\d+")),
        ("checkout_refused", re.compile(r"\[acp\] checkout refused: ")),
        ("settle_refused", re.compile(r"\[acp\] settle refused: "))]
BUYER = [("paid", re.compile(r"\[acp\] paid \d+ through .* settled at ledger #\d+")),
         ("refused", re.compile(r"<- TASK_STATE_REJECTED \| ")),
         ("result_no_deal", re.compile(r"\[market\] ledger #\d+: no deal, stars")),
         ("order_failed", re.compile(r"\[market\] order failed: "))]
# The shops' answers when they turned our buyer down, by reason (checked in this order).
REFUSAL = [("재고: 다른 흥정에 잡혀 있음", re.compile(r"held in another", re.I)),
           ("식품: 남은 유통기한 부족", re.compile(r"shelf life|h left|keep at most", re.I)),
           ("가격: 가게 하한 아래", re.compile(r"No deal|lowest|below|floor|price|meet (that|your) offer|meet at that", re.I)),
           ("재고: 없음 또는 맞는 단위 없음", re.compile(r"in.stock|available|no unit|Reserved|matching|fits your order|not the SKU", re.I)),
           ("조건: 보증이나 등급", re.compile(r"warranty|grade", re.I)),
           ("검증 실패", re.compile(r"verif|signature", re.I))]


def classify_refusal(text: str) -> str:
    said = text.split("TASK_STATE_REJECTED | ", 1)[1]
    for label, rx in REFUSAL:
        if rx.search(said):
            return label
    return "기타"


def quote(row) -> str:
    f, i, ts, text = row
    t = text if len(text) <= 170 else text[:170] + " …"
    return f"`logs/{f}:{i}` ({kst(ts)} KST)\n\n    {t}"


def part3() -> str:
    out = []
    board = json.load(open(FINAL / "board.json", encoding="utf-8"))
    standing = (FINAL / "standing.txt").read_text(encoding="utf-8").splitlines()
    shop = next(s for s in board["shops"] if s["name"] == "Kiro729")
    buyer = next(b for b in board["buyers"] if b["name"] == "Kiro729")

    out.append("### 3-1. 최종 순위\n")
    table = lambda lines: [l for l in lines[1:] if not l.startswith("our ")]
    ours = next(re.match(r"\s*(\d+)\. \S+\s+student_total ([\d.]+)\s+student_pct ([\d.]+)", l) for l in standing if l.endswith("<- us"))
    whens, same = [], []
    for label in ("0000", "0005"):
        other = (LOGS / "round3-final" / label / "standing.txt").read_text(encoding="utf-8").splitlines()
        whens.append(other[0].split(",")[0].replace("board read ", "").split(" ")[1])
        same.append(table(other) == table(standing))
    later = [f"{whens[0]}과 {whens[1]}에 다시 읽은 board(`logs/round3-final/0000`, `0005`)도 "
             + ("표가 같다" if all(same) else "표가 다르다")]
    out.append(f"종료 직전 board(`logs/round3-final/2358/standing.txt`, {standing[0].split(',')[0].replace('board read ', '')} 기준)의 "
               f"Players 표 Class 보기다. {len(table(standing))}명 중 {ours[1]}위, student_total {ours[2]}, 백분위 {ours[3]}다. "
               + ", ".join(later) + ".\n")
    out.append("| 순위 | Player | student_total | student_pct |")
    out.append("|---|---|---|---|")
    for line in standing[1:9]:
        m = re.match(r"\s*(\d+)\. (\S+)\s+student_total ([\d.]+)\s+student_pct ([\d.]+)", line)
        if m:
            name = f"**{m[2]}**" if m[2] == "Kiro729" else m[2]
            out.append(f"| {m[1]} | {name} | {m[3]} | {m[4]} |")
    out.append("\nstanding.txt의 우리 줄:\n")
    us = next(i for i, l in enumerate(standing, 1) if l.endswith("<- us"))
    out.append(f"`logs/round3-final/2358/standing.txt:{us}`\n\n    {standing[us - 1]}\n")

    out.append("### 3-2. 정산 수\n")
    out.append("라운드 전체 숫자는 종료 직전 board의 우리 행에서 읽었다(`logs/round3-final/2358/board.json`).\n")
    out.append("| 쪽 | 항목 | 값 |")
    out.append("|---|---|---|")
    out.append(f"| 가게 | 정산된 판매 (deals) | {n(shop['deals'])} |")
    out.append(f"| 가게 | 매출 (revenue) | {n(shop['revenue'])} |")
    out.append(f"| 가게 | Book (현금 + 재고 원가) | {n(shop['book'])} |")
    out.append(f"| 가게 | 별점 (stars / stars_rank), 리뷰 수 | {shop['stars']} / {shop['stars_rank']}, {n(shop['reviews'])} |")
    out.append(f"| 가게 | 정직한 등급 표시 / 전체 표시 | {n(shop['honest_claims'])} / {n(shop['claims'])} |")
    out.append(f"| 가게 | 환불, 고장 | {n(shop['refunds'])}, {n(shop['broken'])} |")
    out.append(f"| buyer | 받은 주문, 정산한 구매 (deals) | {n(buyer['orders'])}, {n(buyer['deals'])} |")
    out.append(f"| buyer | saved, forum, posts | {buyer['saved']}, {buyer['forum']}, {n(buyer['posts'])} |")
    out.append(f"| 시장 전체 | 정산, 거절(rejected), 이탈(walkaways) | {n(board['totals']['deals'])}, "
               f"{n(board['totals']['rejected'])}, {n(board['totals']['walkaways'])} |\n")

    rows = [r for r in dated_lines() if r[2] <= ROUND_END]
    first, last = rows[0], rows[-1]
    shop_c, buy_c, ref_c = Counter(), Counter(), Counter()
    shop_q, buy_q, ref_q = {}, {}, {}
    chk, setl = Counter(), Counter()
    for r in rows:
        text = r[3]
        for key, rx in SHOP:
            if rx.search(text):
                shop_c[key] += 1
                shop_q.setdefault(key, r)
                if key == "checkout_refused":
                    chk["주문한 단위가 재고에 없음" if "not in stock" in text else text.split("refused: ", 1)[1][:60]] += 1
                if key == "settle_refused":
                    setl[text.split("refused: ", 1)[1][:60]] += 1
                break
        for key, rx in BUYER:
            if rx.search(text):
                buy_c[key] += 1
                buy_q.setdefault(key, r)
                if key == "refused":
                    c = classify_refusal(text)
                    ref_c[c] += 1
                    ref_q.setdefault(c, r)
                break
    hours = (last[2] - first[2]) / 3600
    out.append(f"키트 로그로 셀 수 있는 기간은 {kst(first[2])} ~ {kst(last[2])} KST({hours:.1f}시간)다. 키트는 로그가 20 MB를 넘으면 "
               "`market-kit.log.1`로 넘기고 그 전의 `.1`을 지우기 때문에 그 앞은 남아 있지 않다. 아래 표는 이 기간의 줄 수다"
               "(종료 뒤 줄은 뺐다).\n")
    talks = shop_c["agreed"] + shop_c["rejected"] + shop_c["walked"]
    out.append("| 쪽 | 결과 | 줄 수 | 비율 |")
    out.append("|---|---|---|---|")
    out.append(f"| 가게 흥정 | 합의 (`deal at`) | {n(shop_c['agreed'])} | {shop_c['agreed'] / talks:.1%} |")
    out.append(f"| 가게 흥정 | 우리 하한 아래 제안이라 거절 (`rejected`) | {n(shop_c['rejected'])} | {shop_c['rejected'] / talks:.1%} |")
    out.append(f"| 가게 흥정 | buyer가 떠남 (`walked`) | {n(shop_c['walked'])} | {shop_c['walked'] / talks:.1%} |")
    out.append(f"| 가게 정산 | 시장 /api/settle 통과 (`[acp] settled`) | {n(shop_c['settled'])} | 합의의 {shop_c['settled'] / shop_c['agreed']:.1%} |")
    out.append(f"| buyer | 결제해 정산 (`[acp] paid`) | {n(buy_c['paid'])} | |")
    out.append(f"| buyer | 가게가 거절한 흥정 (`TASK_STATE_REJECTED`) | {n(buy_c['refused'])} | |\n")
    out.append(f"정산 줄 예 (가게, 기간의 첫 줄): {quote(shop_q['settled'])}\n")
    out.append(f"정산 줄 예 (buyer): {quote(buy_q['paid'])}\n")

    out.append("### 3-3. 거부 사유\n")
    out.append("가게가 우리 buyer를 거절할 때 보낸 문장을 사유별로 묶었다. 분류는 `make_report.py`의 `REFUSAL` 규칙이다.\n")
    out.append("| 사유 | 횟수 | 비율 |")
    out.append("|---|---|---|")
    tot = sum(ref_c.values())
    for label, c in ref_c.most_common():
        out.append(f"| {label} | {n(c)} | {c / tot:.1%} |")
    out.append("")
    for label, _ in ref_c.most_common():
        out.append(f"- {label}: {quote(ref_q[label])}\n")
    out.append("우리 가게 쪽에서 시장이나 키트가 거절한 건은 다음과 같다.\n")
    out.append("| 단계 | 사유 | 횟수 |")
    out.append("|---|---|---|")
    for reason, c in chk.most_common():
        out.append(f"| checkout | {reason} | {c} |")
    for reason, c in setl.most_common():
        out.append(f"| settle | {reason} | {c} |")
    out.append("")
    if "checkout_refused" in shop_q:
        out.append(f"checkout 거절 예: {quote(shop_q['checkout_refused'])}\n")
    if "settle_refused" in shop_q:
        out.append(f"settle 거절 예: {quote(shop_q['settle_refused'])}\n")
    after = [r for r in dated_lines() if r[2] > ROUND_END and "order failed: 503" in r[3] and "Round 3 ended at" in r[3]]
    if after:
        out.append(f"종료 뒤에는 시장이 새 주문을 503으로 돌려보냈다({n(len(after))}번, 키트를 멈출 때까지): {quote(after[0])}\n")

    out.append("### 3-4. 10주차 기준 숫자와 비교\n")
    hist = [json.loads(l) for l in open(LOGS / "round3-final" / "board_history.jsonl", encoding="utf-8") if l.strip()]
    hist = [h for h in hist if h.get("ts")]
    then = min(hist, key=lambda h: abs(h["ts"] - WEEK10))
    end = max((h for h in hist if h["ts"] <= ROUND_END), key=lambda h: h["ts"])
    out.append(f"우리 에이전트가 주문 없이 먼저 하는 행동은 buyer의 시세 글(포럼)이다(`analysis.py`의 journal). 기준 숫자는 `analysis.py`가 20분마다 남긴 "
               f"`logs/round3-final/board_history.jsonl`에서 {kst(then['ts'])} KST 줄을, 지금 숫자는 종료 직전 "
               f"{kst(end['ts'])} KST 줄을 읽었다.\n")
    out.append(f"| 숫자 | {kst(then['ts'], '%m/%d')} | {kst(end['ts'], '%m/%d')} |")
    out.append("|---|---|---|")
    pairs = [("buyer posts (쓴 글)", ("buyer", "posts")), ("buyer forum (글 평점)", ("buyer", "forum")),
             ("buyer deals", ("buyer", "deals")), ("가게 deals", ("shop", "deals")), ("가게 revenue", ("shop", "revenue")),
             ("가게 stars", ("shop", "stars"))]
    for label, (side, key) in pairs:
        a, b = (then.get(side) or {}).get(key), (end.get(side) or {}).get(key)
        f = (lambda x: n(x) if isinstance(x, int) else x)
        out.append(f"| {label} | {f(a)} | {f(b)} |")
    ca, cb = then.get("class") or {}, end.get("class") or {}
    out.append(f"| Class 보기 순위 (총점) | {ca.get('place')}위 ({ca.get('total')}) | {cb.get('place')}위 ({cb.get('total')}) |\n")
    return "\n".join(out)


def main() -> int:
    text = part3()
    if "--print" in sys.argv:
        print(text)
        return 0
    report = HERE / "REPORT.md"
    body = report.read_text(encoding="utf-8")
    if START not in body or END not in body:
        print("REPORT.md has no part (3) markers; printing instead\n")
        print(text)
        return 1
    head, rest = body.split(START, 1)
    _, tail = rest.split(END, 1)
    report.write_text(head + START + "\n" + text + "\n" + END + tail, encoding="utf-8")
    print(f"REPORT.md part (3) rewritten ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
