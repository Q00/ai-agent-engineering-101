"""REPORT 5.3: how something the seller side learns reaches the buyer side, drawn on the 5.1 structure with
everything off the route faded. The example is real (11-17, the double-promise settle fault). Sources: the shop
strategist's fact F281 (logs/strategist.log 09:32) and the notes in .market/strategy_exchange.db (11:31, 12:05)."""
import sys
import textwrap
from matplotlib.patches import FancyArrowPatch, Rectangle
from chart_style import fig, save, INK, INK2, SURF
import plot_structure as S

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/comm_example.png"
KEEP = ["seller", "sell_judge", "analyst", "buy_judge", "buyer"]

f, _ = fig(6.6, 6.5)
f.clf()
f.set_facecolor(SURF)
top = f.add_axes([0.01, 0.33, 0.98, 0.66]); top.set_facecolor(SURF)
low = f.add_axes([0.03, 0.0, 0.95, 0.32]); low.set_facecolor(SURF); low.axis("off")

S.draw(top)
top.add_patch(Rectangle((0, 0), 107, 100, fc=SURF, ec="none", alpha=0.72, zorder=10))     # fade everything
for k in KEEP:                                                                              # then the route on top
    S.box(*S.BOXES[k], z=11)
S.db(z=11)


def step(x1, y1, x2, y2):
    top.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=11, lw=2, color=INK,
                                  shrinkA=0, shrinkB=0, zorder=15))


def badge(ax, x, y, n):
    ax.scatter([x], [y], s=150, color=INK, zorder=16, edgecolor=SURF, linewidth=1.2)
    ax.text(x, y, str(n), ha="center", va="center", fontsize=7.5, color=SURF, fontweight="bold", zorder=17)


L, R, BW, BH, DX, DW = S.L, S.R, S.BW, S.BH, S.DX, S.DW
mid_l, mid_r = 38, 58                                    # y of the gaps under and over the judgment row
step(R + BW / 2, 28, DX - DW / 2, 28); badge(top, 94.5, 31.5, 1)               # seller log -> DB
step(DX - DW / 2, 48, R + BW / 2, 48); badge(top, 94.5, 51.5, 2)               # DB -> sell judgment (tools)
step(R + 8, 48 + BH / 2, R + 8, 68 - BH / 2); badge(top, R + 11, mid_r, 3)     # sell judgment -> exchange/analyst
step(L - 8, 68 - BH / 2, L - 8, 48 + BH / 2); badge(top, L - 11, mid_r, 4)     # analyst digest -> buy judgment
step(L - 8, 48 - BH / 2, L - 8, 28 + BH / 2); badge(top, L - 11, mid_l, 5)     # buy judgment -> buyer (params)

steps = [
    ("판매자 → 보관 DB", "정산 거절이 로그로 쌓인다. 11-17 오전 2시간 동안 합의 395건 중 58건이 정산되지 않았다."),
    ("보관 DB → 판매 판단", "09:31 상점 전략가가 툴로 확인: 거절 32건 중 30건이 'unit not in stock', 같은 유닛을 두 번 약속하는 키트 오류."),
    ("판매 판단 → 분석가", "사실과 구매 쪽에 줄 메모를 교환 DB에 남긴다(11:31 \"정산 거절 약 14%의 원인은 키트 오류\")."),
    ("분석가 → 구매 판단", "분석가가 digest에 실어 넘기고, 구매 전략가가 다음 실행에서 읽는다."),
    ("구매 판단 → 구매자", "반영할 것이 있으면 파라미터로 내린다. 이 예에서는 \"구매 쪽에서 보완할 것 없음\"(12:05)."),
]
low.set_xlim(0, 100); low.set_ylim(0, 100)
for i, (route, what) in enumerate(steps):
    y = 90 - i * 19
    badge(low, 1.5, y, i + 1)
    low.text(5, y, route, fontsize=8, color=INK, va="center", fontweight="bold")
    low.text(30, y, textwrap.fill(what, 44), fontsize=7.6, color=INK2, va="center", linespacing=1.4)

save(f, OUT)
print(OUT)
