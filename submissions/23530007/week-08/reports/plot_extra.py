"""REPORT charts for sections 1, 2, 3.4, 3.5, 4, 6 and 7. Sources: data/market_archive.db (board snapshots and the
public ledger), .market/catalog.json, and the incident table of the report.
Run from the week-08 folder: python3 reports/plot_extra.py"""
import json, sqlite3, statistics, collections, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chart_style as cs
import matplotlib.dates as mdates

UTC = dt.timezone.utc
DB = "data/market_archive.db"
OUT = "reports"
ME = "yms979"
END = dt.datetime(2026, 11, 17, 15, 0, tzinfo=UTC).timestamp()


def board_rows():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for ts, d in c.execute("select ts,data from board where ts<=? order by ts", (END + 300,)):
        d = json.loads(d)
        if d.get("round") == 3:
            yield ts, d


def results():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    d = json.loads(c.execute("select data from board order by ts desc limit 1").fetchone()[0])
    P = sorted([p for p in d["players"] if p.get("student")], key=lambda p: -p["student_total"])[:5][::-1]
    f, ax = cs.fig(h=3.0)
    y = range(len(P))
    shop = [p["student_shop"] for p in P]; buyer = [p["student_buyer"] for p in P]
    ax.barh(y, shop, color=cs.US, height=0.6, edgecolor=cs.SURF, lw=2)
    ax.barh(y, buyer, left=shop, color=cs.AQUA, height=0.6, edgecolor=cs.SURF, lw=2)
    names = ["우리" if p["player"] == ME else f"{5 - i}위" for i, p in enumerate(P)]
    ax.set_yticks(list(y)); ax.set_yticklabels(names)
    for i, p in enumerate(P):
        ax.text(p["student_total"] + 0.02, i, f"{p['student_total']:.3f}", va="center", color=cs.INK,
                fontweight="bold" if p["player"] == ME else "normal")
        ax.text(p["student_shop"] / 2, i, f"상점 {p['student_shop']:.2f}", va="center", ha="center", color="white", fontsize=8)
        ax.text(p["student_shop"] + p["student_buyer"] / 2, i, f"구매자 {p['student_buyer']:.2f}", va="center", ha="center", color=cs.INK, fontsize=8)
    ax.set_xlim(0, 2.05); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=cs.GRID, lw=0.8); ax.set_axisbelow(True)
    cs.title(ax, "반 종합 상위 5명: 상점 점수 + 구매자 점수")
    f.tight_layout(); cs.save(f, f"{OUT}/results.png")


def preopen():
    cat = collections.defaultdict(list)
    for it in json.load(open(".market/catalog.json")).values():
        cat[it["category"]].append(it["list_price"])
    med = {k: statistics.median(v) for k, v in cat.items()}
    big = {k: v for k, v in med.items() if v > 300_000}
    n_share = len(big) / len(med); p_share = sum(big.values()) / sum(med.values())
    f, ax = cs.fig(h=2.2)
    rows = [("정가 합계", p_share), ("카테고리 수", n_share)]
    for i, (lab, s) in enumerate(rows):
        ax.barh(i, s, color=cs.US, height=0.55, edgecolor=cs.SURF, lw=2)
        ax.barh(i, 1 - s, left=s, color=cs.OTHER, height=0.55, edgecolor=cs.SURF, lw=2)
        ax.text(max(s, 0.04) + 0.01 if s < 0.1 else s / 2, i, f"{s:.0%}", va="center",
                ha="left" if s < 0.1 else "center", color=cs.INK if s < 0.1 else "white", fontweight="bold")
        ax.text(s + (1 - s) / 2, i, f"{1 - s:.0%}", va="center", ha="center", color=cs.INK2)
    ax.set_yticks([0, 1]); ax.set_yticklabels([r[0] for r in rows]); ax.tick_params(axis="y", length=0)
    ax.set_xticks([]); ax.spines["left"].set_visible(False); ax.spines["bottom"].set_visible(False)
    ax.set_xlim(0, 1)
    ax.text(0, -0.75, f"■ 중앙가 30만 원 넘는 카테고리 {len(big)}개", color=cs.US, fontsize=9)
    ax.text(0.5, -0.75, f"■ 나머지 {len(med) - len(big)}개", color=cs.INK2, fontsize=9)
    cs.title(ax, "카테고리마다 중앙가 상품 1개씩 놓을 때: 비싼 6개가 돈의 60%")
    f.tight_layout(); cs.save(f, f"{OUT}/preopen.png")
    print("preopen", len(big), len(med), round(n_share, 3), round(p_share, 3))


def book_gap(rows):
    out = []
    for ts, d in rows:
        S = [s for s in d["shops"] if not s.get("house") and s.get("book")]
        me = next((s for s in S if s["name"] == ME), None)
        if not me: continue
        other = max((s for s in S if s["name"] != ME), key=lambda s: s["book"])
        out.append((dt.datetime.fromtimestamp(ts, UTC), (me["book"] - other["book"]) / 1e6, other["name"]))
    return out


def defence(gap):
    g = [x for x in gap if x[0] <= dt.datetime(2026, 11, 16, 15, tzinfo=UTC)]
    f, ax = cs.fig()
    t = [x[0] for x in g]; v = [x[1] for x in g]
    ax.fill_between(t, v, color=cs.US, alpha=0.18, lw=0)
    ax.plot(t, v, color=cs.US, lw=2)
    i_max = max(range(len(v)), key=lambda i: v[i])
    ax.annotate(f"최대 +{v[i_max]:.0f}M", xy=(t[i_max], v[i_max]), xytext=(0, 8), textcoords="offset points",
                ha="center", color=cs.INK)
    ax.annotate(f"+{v[-1]:.0f}M", xy=(t[-1], v[-1]), xytext=(6, 0), textcoords="offset points", va="center", color=cs.INK,
                fontweight="bold")
    ax.set_ylim(0, max(v) * 1.15); ax.set_ylabel("장부 격차 (백만 원)")
    ax.grid(axis="y", color=cs.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.DayLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    cs.title(ax, "장부 2위와의 격차, 11-11 ~ 11-16")
    f.tight_layout(); cs.save(f, f"{OUT}/defence.png")
    print("defence", round(v[0]), round(v[i_max]), t[i_max], round(v[-1]))


def endgame(gap):
    a = dt.datetime(2026, 11, 16, 15, tzinfo=UTC)
    g = [x for x in gap if x[0] >= a]
    f, ax = cs.fig()
    t = [x[0] for x in g]; v = [x[1] for x in g]
    T = lambda h, m=0: dt.datetime(2026, 11, 17, h, m, tzinfo=UTC)
    ax.axvspan(T(8, 59), T(10, 29), color=cs.YELLOW, alpha=0.18, lw=0)
    ax.text(T(9, 44), max(v) * 0.82, "종반 매입\n중단", ha="center", va="bottom", color=cs.INK2, fontsize=8)
    ax.plot(t, v, color=cs.US, lw=2, marker="o", ms=2.5)
    for when, lab, ha in [(T(6, 40), "06:40~07:52 보증 끔,\nB·C 판매, 정리 가격 끔 ", "right"), (T(10, 28), " 10:28\n 매입 재개", "left")]:
        ax.axvline(when, color=cs.INK2, lw=0.8, ls=(0, (3, 3)))
        ax.text(when, max(v) * 1.2, lab, color=cs.INK, fontsize=8, va="top", ha=ha)
    i_min = min(range(len(v)), key=lambda i: v[i] if t[i] < T(14) else 1e9)
    ax.annotate(f"+{v[-1]:.1f}M (종료)", xy=(t[-1], v[-1]), xytext=(-4, -14), textcoords="offset points", ha="right",
                color=cs.INK, fontweight="bold")
    ax.set_ylim(0, max(v) * 1.25); ax.set_ylabel("장부 격차 (백만 원)")
    ax.grid(axis="y", color=cs.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H시"))
    cs.title(ax, "마지막 24시간의 장부 격차 (11-16 15시 ~ 11-17 15시 UTC)")
    f.tight_layout(); cs.save(f, f"{OUT}/endgame.png")
    print("endgame", round(v[0], 1), round(min(v), 1), round(v[-1], 1))


def cash_stock(rows):
    t, cash, stock = [], [], []
    for ts, d in rows:
        me = next((s for s in d["shops"] if s["name"] == ME), None)
        if not me: continue
        t.append(dt.datetime.fromtimestamp(ts, UTC)); stock.append(me["stock_cost"] / 1e6); cash.append((me["book"] - me["stock_cost"]) / 1e6)
    f, ax = cs.fig()
    ax.stackplot(t, stock, cash, colors=[cs.US, cs.AQUA], alpha=0.85, lw=0)
    ax.text(t[len(t) // 2], stock[len(t) // 2] / 2, "재고 (원가)", color="white", ha="center", fontweight="bold")
    ax.annotate("현금", xy=(t[2], stock[2] + cash[2] * 0.6), xytext=(18, -6), textcoords="offset points", color=cs.INK,
                fontweight="bold")
    ax.set_ylabel("백만 원"); ax.set_ylim(0, max(a + b for a, b in zip(stock, cash)) * 1.1)
    ax.grid(axis="y", color=cs.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.DayLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    cs.title(ax, "장부의 구성: 현금과 재고, 11-11 ~ 11-17")
    f.tight_layout(); cs.save(f, f"{OUT}/cash_stock.png")


if __name__ == "__main__":
    results(); preopen()
    rows = list(board_rows())
    gap = book_gap(rows)
    defence(gap); endgame(gap); cash_stock(rows)
