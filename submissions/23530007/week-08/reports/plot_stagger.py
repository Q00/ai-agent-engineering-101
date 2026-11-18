"""REPORT 5.2: why the two strategists run 15 minutes apart (a note left by one reaches the other in 15 min, not 30)."""
import sys
from matplotlib.patches import FancyArrowPatch
from chart_style import fig, save, US, AQUA, INK, INK2, GRID

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/stagger.png"

f, axes = fig(6.4, 3.4, nrows=2, sharex=True)
cases = [
    ("같은 시각에 실행", [0, 30, 60], [0, 30, 60], (0, 30), "30분 뒤 반영"),
    ("15분씩 어긋나게 실행", [0, 30, 60], [15, 45], (0, 15), "15분 뒤 반영"),
]
for ax, (name, buy, sell, (a, b), gap) in zip(axes, cases):
    ax.set_ylim(-0.95, 1.35); ax.set_yticks([0, 1]); ax.set_yticklabels(["상점 전략가", "구매 전략가"])
    ax.tick_params(axis="y", length=0, labelsize=8.5)
    for s in ("left", "top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    for y in (0, 1):
        ax.plot([-3, 63], [y, y], color=GRID, lw=1, zorder=0)
    ax.scatter(buy, [1] * len(buy), s=70, color=AQUA, zorder=3, edgecolor="white", linewidth=1.5)
    ax.scatter(sell, [0] * len(sell), s=70, color=US, zorder=3, edgecolor="white", linewidth=1.5)
    ax.add_patch(FancyArrowPatch((a, 1), (b, 0), arrowstyle="-|>", mutation_scale=10, lw=1.2, color=INK,
                                 shrinkA=6, shrinkB=6))
    ax.add_patch(FancyArrowPatch((a, -0.5), (b, -0.5), arrowstyle="|-|", mutation_scale=3, lw=0.9, color=INK2,
                                 shrinkA=0, shrinkB=0))
    ax.text((a + b) / 2, -0.75, gap, fontsize=8.5, color=INK, ha="center", va="center")
    ax.set_title(name, loc="left", fontsize=9.5, color=INK, pad=2)
axes[1].set_xlim(-3, 63)
axes[1].set_xticks([0, 15, 30, 45, 60]); axes[1].set_xticklabels([":00", ":15", ":30", ":45", ":00"])
axes[0].tick_params(axis="x", length=0)
axes[1].tick_params(axis="x", length=0)
f.text(0.99, 0.015, "화살표: 구매 전략가가 :00에 남긴 정보가 상점 전략가의 판단에 들어가는 시점", ha="right",
       fontsize=7, color=INK2)
f.subplots_adjust(left=0.15, right=0.98, top=0.92, bottom=0.17, hspace=0.55)
save(f, OUT)
print(OUT)
