"""REPORT 5.2: what our buyer hands our shop through market_intel.jsonl, and what goes back the other way."""
import json
import sys
from collections import Counter
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from chart_style import fig, save, US, AQUA, INK, INK2, GRID

INTEL = sys.argv[1] if len(sys.argv) > 1 else "market_intel.jsonl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "reports/exchange.png"
BUY_FILL, SELL_FILL, NEU_FILL = "#e3f4ec", "#fbe6dc", "#f1f0ec"

n = Counter()
for line in open(INTEL, encoding="utf-8"):
    try:
        n[json.loads(line).get("type", "ask")] += 1
    except ValueError:
        pass

f, ax = fig(6.4, 4.3)
ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")


def rbox(x, y, w, h, fill, edge):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=2", fc=fill, ec=edge, lw=1.2))


def arrow(x1, y1, x2, y2, dashed=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9, lw=1, color=INK2,
                                 linestyle=(0, (3, 2)) if dashed else "-", shrinkA=0, shrinkB=0))


def side(x, y, w, h, fill, edge, name, sub):
    rbox(x, y - h / 2, w, h, fill, edge)
    ax.text(x + w / 2, y + 3, name, ha="center", va="center", fontsize=8.5, color=INK, fontweight="bold")
    ax.text(x + w / 2, y - 2.6, sub, ha="center", va="center", fontsize=6.8, color=INK2, linespacing=1.3)


def middle(y, what, sub, edge=GRID, dashed=False):
    ax.add_patch(FancyBboxPatch((29, y - 6), 42, 12, boxstyle="round,pad=0,rounding_size=2", fc=NEU_FILL, ec=edge,
                                lw=1.2, linestyle=(0, (3, 2)) if dashed else "-"))
    ax.text(50, y + 1.8, what, ha="center", va="center", fontsize=8.5, color=INK)
    ax.text(50, y - 3.0, sub, ha="center", va="center", fontsize=7.2, color=INK2)


# buyer -> shop: one file, three kinds of lines
ax.text(1, 96, "구매자 → 상점", fontsize=9, color=INK, fontweight="bold", va="center")
ax.text(50, 96, "market_intel.jsonl", ha="center", fontsize=8.5, color=INK2, va="center")
rbox(1, 39, 19, 50, BUY_FILL, AQUA)
ax.text(10.5, 70, "구매자", ha="center", fontsize=10, color=INK, fontweight="bold")
ax.text(10.5, 64, "market_buyer", ha="center", fontsize=7.5, color=INK2)
ax.text(10.5, 52, "주문·흥정마다\n한 줄씩 기록", ha="center", va="center", fontsize=7.5, color=INK2, linespacing=1.5)
rows = [
    (82, "다른 상점의 첫 호가", n["ask"], "seller_r3", "우리 첫 호가를\n그 중앙값보다 조금 아래로"),
    (64, "식품 주문의 사용 시간", n["need"], "restock", "그때까지 버틸\n식품만 매입"),
    (46, "주문 가격대와 충족 여부", n["order"], "restock", "아무도 못 채운\n가격대를 재고로"),
]
for y, what, cnt, prog, use in rows:
    middle(y, what, f"{cnt:,}줄")
    arrow(20, y, 29, y)
    arrow(71, y, 77, y)
    side(77, y, 22, 14, SELL_FILL, US, prog, use)

# shop -> buyer: no file, an observation from our own sales log, applied to the buyer
ax.plot([1, 99], [32, 32], color=GRID, lw=0.8)
ax.text(1, 26, "상점 → 구매자", fontsize=9, color=INK, fontweight="bold", va="center")
side(1, 12, 19, 15, BUY_FILL, AQUA, "구매자", "거절하는 상점에는\n숫자만 보내기")
middle(12, "상점은 첫 문장 모양으로 구매자를 알아본다", "파일 없음 · 운영자가 판매 로그를 보고 옮김", dashed=True)
side(77, 12, 22, 15, SELL_FILL, US, "판매 로그", "우리 상점도 같은\n방식으로 구분")
arrow(77, 12, 71, 12, dashed=True)
arrow(29, 12, 20, 12, dashed=True)

f.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
save(f, OUT)
print(OUT, dict(n))
