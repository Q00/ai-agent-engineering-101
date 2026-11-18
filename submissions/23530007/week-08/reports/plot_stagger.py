"""REPORT 5.2: why the two strategists run 15 minutes apart. One panel per schedule: two lanes, a chip per run,
the question (buy -> shop) and the answer (shop -> buy) as arcs, and the round trip as a big number on the right."""
import sys
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from chart_style import fig, save, US, AQUA, INK, INK2, GRID, SURF

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/stagger.png"
BUY_FILL, SELL_FILL = "#e3f4ec", "#fbe6dc"
X0, X1 = -6, 62          # the hour on the x axis (minutes)
HERO = 78                # where the round-trip number sits

f, ax = fig(6.4, 3.9)
ax.set_xlim(-24, 92); ax.set_ylim(-1, 101); ax.axis("off")


def chip(x, y, text, edge):
    ax.add_patch(FancyBboxPatch((x - 3.6, y - 3.2), 7.2, 6.4, boxstyle="round,pad=0,rounding_size=1.6",
                                fc=SURF, ec=edge, lw=1.4, zorder=3))
    ax.text(x, y, text, ha="center", va="center", fontsize=7.5, color=INK, zorder=4)


def hop(x1, y1, x2, y2, label, rad):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=1.3, color=INK,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=11, shrinkB=11, zorder=2))
    ax.text((x1 + x2) / 2, (y1 + y2) / 2, label, ha="center", va="center", fontsize=7, color=INK2,
            bbox=dict(boxstyle="round,pad=0.25", fc=SURF, ec="none"), zorder=2)


def panel(top, title, buy, sell, path, trip):
    yb, ys = top - 14, top - 32                       # lane centres
    ax.text(-24, top, title, fontsize=9.5, color=INK, fontweight="bold", va="center")
    for y, fill, name in ((yb, BUY_FILL, "구매 전략가"), (ys, SELL_FILL, "상점 전략가")):
        ax.add_patch(FancyBboxPatch((X0, y - 5), X1 - X0, 10, boxstyle="round,pad=0,rounding_size=2.5",
                                    fc=fill, ec="none", zorder=0))
        ax.text(-8, y, name, ha="right", va="center", fontsize=8, color=INK2)
    for t in buy:
        chip(t, yb, f":{t % 60:02d}", AQUA)
    for t in sell:
        chip(t, ys, f":{t % 60:02d}", US)
    (qa, qb), (aa, ab) = path                          # question buy->shop, answer shop->buy
    hop(qa, yb, qb, ys, "질문", -0.18)
    hop(aa, ys, ab, yb, "답", -0.18)
    # round trip: bracket over the buyer lane, big number on the right
    ax.plot([qa, qa, ab, ab], [yb + 6.5, yb + 8, yb + 8, yb + 6.5], color=INK2, lw=0.9)
    ax.text(HERO, (yb + ys) / 2 + 2.5, f"{trip}분", ha="center", va="center", fontsize=20, color=INK,
            fontweight="bold")
    ax.text(HERO, (yb + ys) / 2 - 6, "묻고 답을 받기까지", ha="center", va="center", fontsize=7, color=INK2)


panel(97, "같은 시각에 실행", [0, 30, 60], [0, 30, 60], ((0, 30), (30, 60)), 60)
ax.plot([-24, 92], [52, 52], color=GRID, lw=0.8)
panel(46, "15분씩 어긋나게 실행", [0, 30, 60], [15, 45], ((0, 15), (15, 30)), 30)

f.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
save(f, OUT)
print(OUT)
