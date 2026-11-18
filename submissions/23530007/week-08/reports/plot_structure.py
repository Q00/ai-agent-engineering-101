"""REPORT 5.1: the agent hierarchy as one clean picture (user and observers, analyst, buy/sell judgment, buyer/seller)."""
import sys
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from chart_style import fig, save, US, AQUA, INK, INK2, GRID, SURF

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/structure.png"
BUY_FILL, SELL_FILL, NEU_FILL = "#e3f4ec", "#fbe6dc", "#f1f0ec"

f, ax = fig(6.4, 4.6)
ax.set_xlim(0, 100); ax.set_ylim(0, 101.5); ax.axis("off")


def box(cx, cy, w, h, name, sub, fill, edge):
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h, boxstyle="round,pad=0,rounding_size=2.2",
                                fc=fill, ec=edge, lw=1.2))
    ax.text(cx, cy + 2.3, name, ha="center", va="center", fontsize=10, color=INK, fontweight="bold")
    ax.text(cx, cy - 3.2, sub, ha="center", va="center", fontsize=7.5, color=INK2)


def arrow(x1, y1, x2, y2, dashed=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=1, color=INK2,
                                 linestyle=(0, (3, 2)) if dashed else "-", shrinkA=0, shrinkB=0))


# layer labels on the left
rows = [(88, "사람 · 감시"), (68, "분석"), (48, "판단"), (28, "실행"), (9, "시장")]
for y, t in rows:
    ax.text(2, y, t, ha="left", va="center", fontsize=8.5, color=INK2)
for y in (78, 58, 38, 18):
    ax.plot([2, 98], [y, y], color=GRID, lw=0.8)

L, R, BW, BH = 41, 77, 30, 13
box(L, 88, BW, BH, "사용자", "목표 · 휴대폰 지시", NEU_FILL, INK2)
box(R, 88, BW, BH, "관측자", "가드 · A/B · 1위 확률", NEU_FILL, INK2)
box(59, 68, 66, BH, "분석가 (큐레이터)", "원장 · 보드 정리, 전략가 요청에 답", NEU_FILL, INK2)
box(L, 48, BW, BH, "구매 판단", "구매 전략가 · 30분마다", BUY_FILL, AQUA)
box(R, 48, BW, BH, "판매 판단", "상점 전략가 · 30분마다", SELL_FILL, US)
box(L, 28, BW, BH, "구매자", "market_buyer · review", BUY_FILL, AQUA)
box(R, 28, BW, BH, "판매자", "seller_r3 · restock", SELL_FILL, US)
ax.add_patch(FancyBboxPatch((26, 4), 66, 10, boxstyle="round,pad=0,rounding_size=2.2", fc=INK, ec=INK))
ax.text(59, 9, "Agent Market", ha="center", va="center", fontsize=10, color=SURF, fontweight="bold")

# downward flow
arrow(L, 88 - BH / 2, L, 68 + BH / 2)
arrow(R, 88 - BH / 2, R, 68 + BH / 2)
arrow(L, 68 - BH / 2, L, 48 + BH / 2)
arrow(R, 68 - BH / 2, R, 48 + BH / 2)
arrow(L, 48 - BH / 2, L, 28 + BH / 2)
arrow(R, 48 - BH / 2, R, 28 + BH / 2)
arrow(L, 28 - BH / 2, L, 14)
arrow(R, 28 - BH / 2, R, 14)
ax.text(L + 1.5, 38, "파라미터", fontsize=7, color=INK2, va="center")
ax.text(R + 1.5, 38, "파라미터", fontsize=7, color=INK2, va="center")
# ntfy: the user talks with the observers and both strategists through phone pushes (dashed, both ways)
def ntfy(pts, lx, ly, **kw):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=INK, lw=1, linestyle=(0, (3, 2)))
    for a, b in ((pts[-2], pts[-1]), (pts[1], pts[0])):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=9, lw=0, color=INK, shrinkA=0, shrinkB=0))
    ax.text(lx, ly, "ntfy", fontsize=7, color=INK, ha="center", va="center",
            bbox=dict(boxstyle="round,pad=0.2", fc=SURF, ec="none"), **kw)


ntfy([(R - BW / 2, 88), (L + BW / 2, 88)], 59, 91.5)                                   # observers <-> user
ntfy([(L - BW / 2, 48), (21, 48), (21, 88), (L - BW / 2, 88)], 21, 68, rotation=90)    # buy judgment <-> user
ntfy([(R + BW / 2, 48), (96, 48), (96, 99), (L, 99), (L, 88 + BH / 2)], 96, 68, rotation=-90)  # sell judgment <-> user

f.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
save(f, OUT)
print(OUT)
