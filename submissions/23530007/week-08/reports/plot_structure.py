"""REPORT 5.1: the agent hierarchy as one clean picture (user and observers, analyst, buy/sell judgment, buyer/seller),
with the archive DB as its own column on the right: the market ledger and our logs go in, the analyst and observers read it."""
import sys
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Ellipse, Rectangle, Arc
from chart_style import fig, save, US, AQUA, INK, INK2, GRID, SURF

BUY_FILL, SELL_FILL, NEU_FILL = "#e3f4ec", "#fbe6dc", "#f1f0ec"
FAINT, FAINT_FILL = "#bdbcb6", "#f6f5f2"
L, R, BW, BH = 41, 77, 30, 13              # column centres, box size
DX, DW, DY0, DY1, E = 101, 8, 4, 76, 2.2  # archive DB column
ax = None


def box(cx, cy, w, h, name, sub, fill, edge, z=1):
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h, boxstyle="round,pad=0,rounding_size=2.2",
                                fc=fill, ec=edge, lw=1.2, zorder=z))
    ax.text(cx, cy + 2.3, name, ha="center", va="center", fontsize=10, color=INK, fontweight="bold", zorder=z)
    ax.text(cx, cy - 3.2, sub, ha="center", va="center", fontsize=7.5, color=INK2, zorder=z)


# every box of the picture by name, so another figure can redraw some of them on top (plot_comm_example.py)
BOXES = {
    "user": (L, 88, BW, BH, "사용자", "목표 · 휴대폰 지시", NEU_FILL, INK2),
    "observer": (R, 88, BW, BH, "관측자", "가드 · A/B · 1위 확률", NEU_FILL, INK2),
    "analyst": (59, 68, 66, BH, "분석가 (큐레이터)", "원장 · 보드 정리, 전략가 요청에 답", NEU_FILL, INK2),
    "buy_judge": (L, 48, BW, BH, "구매 판단", "구매 전략가 · 30분마다", BUY_FILL, AQUA),
    "sell_judge": (R, 48, BW, BH, "판매 판단", "상점 전략가 · 30분마다", SELL_FILL, US),
    "buyer": (L, 28, BW, BH, "구매자", "market_buyer · review", BUY_FILL, AQUA),
    "seller": (R, 28, BW, BH, "판매자", "seller_r3 · restock", SELL_FILL, US),
}


def db(z=1):
    """The archive DB: a thin, quiet column on the right (light lines; it stores, it does not decide)."""
    ax.add_patch(Rectangle((DX - DW / 2, DY0 + E / 2), DW, DY1 - DY0 - E, fc=FAINT_FILL, ec="none", zorder=z))
    for x in (DX - DW / 2, DX + DW / 2):
        ax.plot([x, x], [DY0 + E / 2, DY1 - E / 2], color=FAINT, lw=0.9, zorder=z + 1)
    ax.add_patch(Ellipse((DX, DY0 + E / 2), DW, E, fc=FAINT_FILL, ec="none", zorder=z))
    ax.add_patch(Arc((DX, DY0 + E / 2), DW, E, theta1=180, theta2=360, color=FAINT, lw=0.9, zorder=z + 1))
    ax.add_patch(Ellipse((DX, DY1 - E / 2), DW, E, fc=FAINT_FILL, ec=FAINT, lw=0.9, zorder=z + 2))
    ax.text(DX, 40, "보관 DB", ha="center", va="center", fontsize=7.5, color=INK2, rotation=-90, zorder=z + 3)


def arrow(x1, y1, x2, y2, dashed=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=1, color=INK2,
                                 linestyle=(0, (3, 2)) if dashed else "-", shrinkA=0, shrinkB=0))


def faint(x1, y1, x2, y2):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=7, lw=0.8, color=FAINT,
                                 shrinkA=0, shrinkB=0))


def draw(target):
    """Draw the structure on target (an axes with x 0-107, y 0-100)."""
    global ax
    ax = target
    ax.set_xlim(0, 107); ax.set_ylim(0, 100); ax.axis("off")
    # layer labels on the left
    rows = [(88, "사람 · 감시"), (68, "분석"), (48, "판단"), (28, "실행"), (9, "시장")]
    for y, t in rows:
        ax.text(2, y, t, ha="left", va="center", fontsize=8.5, color=INK2)
    for y in (78, 58, 38, 18):
        ax.plot([2, 95], [y, y], color=GRID, lw=0.8)

    for b in BOXES.values():
        box(*b)
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
    db()

    # in: the market's public ledger, our programs' logs
    faint(92, 9, DX - DW / 2, 9)
    faint(R + BW / 2, 28, DX - DW / 2, 28)
    # out: the analyst and the observers read it
    faint(DX - DW / 2, 68, 92, 68)
    ax.plot([DX, DX], [DY1, 88], color=FAINT, lw=0.8)
    faint(DX, 88, R + BW / 2, 88)

    # ntfy: the user and the observers talk through phone pushes (light dashed, both ways)
    ax.annotate("", xy=(L + BW / 2, 88), xytext=(R - BW / 2, 88),
                arrowprops=dict(arrowstyle="<|-|>", mutation_scale=7, lw=0.8, color=INK2, linestyle=(0, (2, 2)),
                                shrinkA=0, shrinkB=0))
    ax.text(59, 91.3, "ntfy", fontsize=7, color=INK2, ha="center", va="center")


if __name__ == "__main__":
    OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/structure.png"
    f, a = fig(6.6, 4.6)
    draw(a)
    f.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    save(f, OUT)
    print(OUT)
