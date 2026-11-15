"""Regenerates the numbers of REPORT.md part 3 from the files in this folder, so a grader can re-derive them:

    python metrics.py            # markdown tables to stdout

Sources: logs/market-kit.log (kit + hooks), .market/payments.jsonl (why the kit did not pay), logs/tuner.log,
and the live board (https://market.ourolabs.ai/api/board) for the rank; pass --offline to skip the board.
"""
import collections, json, re, sys, urllib.request
from pathlib import Path

LOG = Path("logs/market-kit.log")


def log_lines():
    return LOG.read_text(encoding="utf-8", errors="ignore").splitlines() if LOG.exists() else []


def by_day(lines):
    """The log has no dates: a day starts where the clock wraps past midnight."""
    days, day, last = [], [], ""
    for l in lines:
        t = l[:8]
        if not re.match(r"\d\d:\d\d:\d\d", t):
            continue
        if last and t < last and (int(last[:2]) - int(t[:2])) > 12:
            days.append(day); day = []
        day.append(l); last = t
    days.append(day)
    return days


def table_days(days):
    rows = ["| 날짜(로그상 n일째) | 가게 방문 | 합의 | 정산 | 결렬 | 마진 | buyer 주문 | 결제 | 결제 거부 |", "|---|---|---|---|---|---|---|---|---|"]
    for i, d in enumerate(days, 1):
        txt = "\n".join(d)
        deals = [(int(a), int(c)) for a, c in re.findall(r"\[deal\] \S+ grade \w at (\d+) \(cost (\d+)\)", txt)]
        cost = sum(c for _, c in deals)
        margin = f"{100 * (sum(a for a, _ in deals) - cost) / cost:.0f}%" if cost else "-"
        n = lambda pat: txt.count(pat)
        rows.append(f"| {i} | {n('[visit]')} | {len(deals)} | {n('[acp] settled')} | {n('[walk]')} | {margin} | "
                    f"{n('[order o-')} | {n('[acp-buyer] done: paid')} | {n('done: not paying')} |")
    return "\n".join(rows)


def table_refusals():
    c = collections.Counter()
    for l in Path(".market/payments.jsonl").read_text(encoding="utf-8").splitlines() if Path(".market/payments.jsonl").exists() else []:
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if r.get("paid") is False:
            why = re.sub(r"[0-9.]+ h of shelf life left, the policy asks for [0-9.]+", "N h of shelf life left < asked", r.get("reason", "?"))
            c[why[:70]] += 1
    rows = ["| 키트가 결제를 거부한 이유 (payments.jsonl) | 건수 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in c.most_common(8)]
    return "\n".join(rows)


def table_tuner():
    rows = ["| 시각 | 바꾼 손잡이 | 이유 |", "|---|---|---|"]
    for l in Path("logs/tuner.log").read_text(encoding="utf-8").splitlines() if Path("logs/tuner.log").exists() else []:
        d = json.loads(l)
        ch = {k: (d["from"][k], d["to"][k]) for k in d["to"] if d["from"][k] != d["to"][k]}
        rows.append(f"| {d['t']} | {ch or '유지'} | {d['why'][:90]} |")
    return "\n".join(rows)


def board():
    b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board", timeout=20))
    s = next(r for r in b["shops"] if r["name"] == "yeonsong2"); u = next(r for r in b["buyers"] if r["name"] == "yeonsong2")
    stu = [x for x in b["players"] if not x.get("house")]; k = next(i for i, x in enumerate(stu) if x["player"] == "yeonsong2")
    return (f"- Players(학생): **{k + 1} / {len(stu)}**, 합계 {stu[k]['total']:.2f} (중앙값 {stu[len(stu) // 2]['total']:.2f}, 20위 {stu[19]['total']:.2f})\n"
            f"- 가게 ALL {s['all']}: 정산 {s['deals']:,}, 매출 {s['revenue']:,}, Book {s['book']:,}, 별점 {s['stars']}, 정직 주장 {s['honest_claims']}/{s['claims']}\n"
            f"- buyer ALL {u['all']}: 주문 {u['orders']:,}, 정산 {u['deals']:,}, Saved {u['saved']}, 게시판 {u['forum']}, 글 {u['posts']:,}")


if __name__ == "__main__":
    days = by_day(log_lines())
    print("### 날짜별 지표 (logs/market-kit.log)\n" + table_days(days) + "\n")
    print("### 결제 거부 사유\n" + table_refusals() + "\n")
    print("### tuner 결정 (logs/tuner.log)\n" + table_tuner() + "\n")
    if "--offline" not in sys.argv:
        print("### 보드 (조회 시점)\n" + board())
