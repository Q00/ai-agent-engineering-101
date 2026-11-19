"""REPORT 5.2: why the two strategists run 15 minutes apart. Each sends the other what helps its judgment; one panel
per schedule shows how long a note waits before the other side's next run uses it."""
import sys
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from chart_style import fig, save, US, AQUA, INK, INK2, GRID, SURF

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/stagger.png"
BUY_FILL, SELL_FILL = "#e3f4ec", "#fbe6dc"
X0, X1 = -6, 66
HERO = 82

f, ax = fig(6.4, 3.9)
ax.set_xlim(-24, 96); ax.set_ylim(-1, 101); ax.axis("off")


def chip(x, y, text, edge):
    ax.add_patch(FancyBboxPatch((x - 3.6, y - 3.2), 7.2, 6.4, boxstyle="round,pad=0,rounding_size=1.6",
                                fc=SURF, ec=edge, lw=1.4, zorder=3))
    ax.text(x, y, text, ha="center", va="center", fontsize=7.5, color=INK, zorder=4)


def send(x1, y1, x2, y2, label, rad=0.12):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=1.3, color=INK,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=11, shrinkB=11, zorder=2))
    # the label sits on the curve: arc3 bends in display space, so find its midpoint there
    (px1, py1), (px2, py2) = ax.transData.transform([(x1, y1), (x2, y2)])
    mx, my = (px1 + px2) / 2 + rad * (py2 - py1) / 2, (py1 + py2) / 2 - rad * (px2 - px1) / 2
    lx, ly = ax.transData.inverted().transform((mx, my))
    ax.text(lx, ly, label, ha="center", va="center", fontsize=7, color=INK,
            bbox=dict(boxstyle="round,pad=0.25", fc=SURF, ec="none"), zorder=2)


def panel(top, title, shop, buy, hops, wait):
    ys, yb = top - 14, top - 32                       # shop lane on top: the note flows down to the buyer
    ax.text(-24, top, title, fontsize=9.5, color=INK, fontweight="bold", va="center")
    for y, fill, name in ((ys, SELL_FILL, "상점 전략가"), (yb, BUY_FILL, "구매 전략가")):
        ax.add_patch(FancyBboxPatch((X0, y - 5), X1 - X0, 10, boxstyle="round,pad=0,rounding_size=2.5",
                                    fc=fill, ec="none", zorder=0))
        ax.text(-8, y, name, ha="right", va="center", fontsize=8, color=INK2)
    for t in shop:
        chip(t, ys, f":{t % 60:02d}", US)
    for t in buy:
        chip(t, yb, f":{t % 60:02d}", AQUA)
    for (a, b), down in hops:                         # down: shop -> buyer, else buyer -> shop
        if down:
            send(a, ys, b, yb, f"{b - a}분", 0.18)
        else:
            send(a, yb, b, ys, f"{b - a}분", -0.18)
    ax.text(HERO, (ys + yb) / 2 + 2.5, f"{wait}분", ha="center", va="center", fontsize=20, color=INK,
            fontweight="bold")
    ax.text(HERO, (ys + yb) / 2 - 6, "정보가 판단에 쓰이기까지", ha="center", va="center", fontsize=7, color=INK2)


panel(97, "같은 시각에 실행", [0, 30, 60], [0, 30, 60], [((0, 30), True), ((0, 30), False), ((30, 60), True), ((30, 60), False)], 30)
ax.plot([-24, 96], [52, 52], color=GRID, lw=0.8)
panel(46, "15분씩 어긋나게 실행", [15, 45], [0, 30, 60], [((0, 15), False), ((15, 30), True), ((30, 45), False), ((45, 60), True)], 15)

f.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
save(f, OUT)
print(OUT)
