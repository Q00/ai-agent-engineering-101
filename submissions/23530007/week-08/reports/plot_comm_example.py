"""REPORT 5.3: one real exchange (11-17, the double-promise settle fault) drawn on the 5.1 structure.
Numbered dark arrows are the steps; the list under the picture says what each one carried. Sources: the shop
strategist's fact F281 (logs/strategist.log 09:32) and the notes in .market/strategy_exchange.db (10:01-12:05)."""
import sys
import textwrap
from matplotlib.patches import FancyArrowPatch
from chart_style import fig, save, INK, INK2, SURF
import plot_structure as S

OUT = sys.argv[1] if len(sys.argv) > 1 else "reports/comm_example.png"

f, _ = fig(6.6, 7.0)
f.clf()
f.set_facecolor(SURF)
top = f.add_axes([0.01, 0.37, 0.98, 0.62]); top.set_facecolor(SURF)
low = f.add_axes([0.02, 0.0, 0.96, 0.36]); low.set_facecolor(SURF); low.axis("off")
S.draw(top)


def step(x1, y1, x2, y2):
    top.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=11, lw=2, color=INK,
                                  shrinkA=0, shrinkB=0, zorder=6))


def badge(x, y, n):
    top.scatter([x], [y], s=150, color=INK, zorder=7, edgecolor=SURF, linewidth=1.2)
    top.text(x, y, str(n), ha="center", va="center", fontsize=7.5, color=SURF, fontweight="bold", zorder=8)


L, R, BW, BH, DX, DW = S.L, S.R, S.BW, S.BH, S.DX, S.DW
gap_l, gap_r = L + BW / 2, R - BW / 2                      # the gap between the two judgment boxes
step(DX - DW / 2, 48, R + BW / 2, 48); badge(DX - DW / 2 - 2.2, 51.5, 1)          # DB -> sell judgment
step(gap_r, 50.5, gap_l, 50.5)                                                  # sell -> buy notes
badge((gap_l + gap_r) / 2 - 1.8, 56.5, 2); badge((gap_l + gap_r) / 2 + 1.8, 56.5, 4)
step(R + 9, 48 + BH / 2, R + 9, 68 - BH / 2); badge(R + 12, 58, 3)              # sell judgment -> analyst
step(gap_l, 45.5, gap_r, 45.5); badge((gap_l + gap_r) / 2, 40, 5)                # buy -> sell (reply)

steps = [
    ("09:31", "판매 판단", "보관 DB의 정산 기록을 툴로 확인: 2시간 동안 합의 395건 중 58건이 정산되지 않았고, 거절 32건 중 30건이 "
                         "'unit not in stock'. 같은 유닛을 두 번 약속하는 키트 오류로 보고 사실로 남김"),
    ("10:00", "구매 판단", "판매 판단이 남긴 사실을 읽고 \"구매 쪽 파라미터와는 무관\"하다고 판단"),
    ("10:30", "판매 판단", "가격 파라미터로는 고칠 수 없는 키트 문제라서 분석가에게 올림"),
    ("11:31", "판매 판단", "구매 쪽에 \"정산 거절 약 14%의 원인은 키트 오류\"라고 알림"),
    ("12:05", "구매 판단", "받았다고 답하고, 구매 쪽에서 보완할 것은 없다고 남김"),
]
low.set_xlim(0, 100); low.set_ylim(0, 100)
low.text(0, 97, "예: 11-17 정산 오류가 두 전략가 사이를 오간 순서", fontsize=9, color=INK, fontweight="bold", va="center")
for i, (t, who, what) in enumerate(steps):
    y = 82 - i * 17
    low.scatter([2], [y], s=150, color=INK, edgecolor=SURF)
    low.text(2, y, str(i + 1), ha="center", va="center", fontsize=7.5, color=SURF, fontweight="bold")
    low.text(6, y, f"{t}  {who}", fontsize=8, color=INK, va="center", fontweight="bold")
    low.text(24, y, textwrap.fill(what, 46), fontsize=7.6, color=INK2, va="center", linespacing=1.4)

save(f, OUT)
print(OUT)
