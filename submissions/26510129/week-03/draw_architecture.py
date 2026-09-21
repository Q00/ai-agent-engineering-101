import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib import font_manager

# Korean font
font_manager.fontManager.addfont('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
plt.rcParams["font.family"] = font_manager.FontProperties(fname='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc').get_name()
plt.rcParams["axes.unicode_minus"] = False

INK = "#1f2937"; MUTED = "#6b7280"; LINE = "#9ca3af"
MGR = "#dbeafe"; MGR_E = "#2563eb"
A_C = "#fee2e2"; A_E = "#dc2626"
B_C = "#dcfce7"; B_E = "#16a34a"
C_C = "#fef3c7"; C_E = "#d97706"
IO  = "#f3f4f6"; IO_E = "#6b7280"

fig, ax = plt.subplots(figsize=(15, 8.6), dpi=200)
ax.set_xlim(0, 150); ax.set_ylim(0, 86); ax.axis("off")
fig.patch.set_facecolor("white")

def box(x, y, w, h, fc, ec, title, lines=(), title_size=11, size=8.6, lw=1.6, radius=1.2):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                       fc=fc, ec=ec, lw=lw)
    ax.add_patch(p)
    ax.text(x + w/2, y + h - 3.2, title, ha="center", va="center",
            fontsize=title_size, fontweight="bold", color=INK)
    for i, ln in enumerate(lines):
        ax.text(x + 2.2, y + h - 7.2 - i*3.6, ln, ha="left", va="center",
                fontsize=size, color=INK)

def arrow(x1, y1, x2, y2, color, label=None, lx=0, ly=0, style="-|>", ls="-", size=8):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=14,
                        color=color, lw=1.5, linestyle=ls, shrinkA=0, shrinkB=0)
    ax.add_patch(a)
    if label:
        ax.text((x1+x2)/2 + lx, (y1+y2)/2 + ly, label, ha="center", va="center",
                fontsize=size, color=color,
                bbox=dict(fc="white", ec="none", pad=1.2))

# title
ax.text(2, 83, "Week 03 · Contract Net 구조 — manager 1 + LLM contractor 3",
        fontsize=14, fontweight="bold", color=INK, va="center")
ax.text(2, 79.6, "탐정 사무소: 단서(lead) 하나 = 태스크 하나. manager는 담당 형사, contractor는 물증·면담·기록 전문가",
        fontsize=9.5, color=MUTED, va="center")

# ---- tasks.json (left)
box(2, 40, 24, 32, IO, IO_E, "tasks.json",
    ["9 leads, gold A3 / B3 / C3",
     "plain 5 · trap 4",
     "gold 규칙:",
     "  물건을 봐야 → A",
     "  사람에게 물어야 → B",
     "  기록을 찾아야 → C"], size=8.2)

# ---- manager (center)
box(34, 38, 40, 34, MGR, MGR_E, "manager  ·  manager.run_round()",
    ["파이썬 함수. LLM 아님. 판단하지 않고 숫자만 비교",
     "",
     "태스크마다:",
     "  ① TASK-ANNOUNCEMENT를 A, B, C에 방송   (msg +3)",
     "  ② JSON 입찰 수집. bid=true만 남김        (msg +입찰수)",
     "  ③ confidence 최고에 낙찰. 동점 → 먼저 답한 쪽 (msg +1)",
     "  ④ 낙찰자 == gold ? correct : misaward",
     "      입찰 0건 → unassigned · JSON 아님 → parse_fail"], size=8.2)

# ---- contractors (right)
cx, cw, ch = 96, 50, 11.5
box(cx, 60, cw, ch, A_C, A_E, "contractor A  —  forensics (물증)",
    ["examine physical objects, traces and residues"], size=8.2)
box(cx, 46, cw, ch, B_C, B_E, "contractor B  —  interviewing (면담)",
    ["talk to witnesses and suspects"], size=8.2)
box(cx, 32, cw, ch, C_C, C_E, "contractor C  —  records (기록)",
    ["search registries, logs, ledgers and databases"], size=8.2)
ax.text(cx + cw/2, 29.0, '- - → 회신: {"bid": true/false, "confidence": 0–100, "reason": "..."}  JSON 하나만, 재시도 없음',
        ha="center", fontsize=8, color=MUTED)
ax.text(cx + cw/2, 25.8, "각각 = system prompt 한 줄 + 공고당 gpt-4o-mini 호출 1회 · 기억·도구·서로의 존재 없음",
        ha="center", fontsize=8, color=MUTED)

# arrows tasks -> manager
arrow(26, 57, 34, 57, IO_E, "lead 1..9", ly=2.6)

# arrows manager <-> contractors
for yc, col in ((65.75, A_E), (51.75, B_E), (37.75, C_E)):
    arrow(74, 58 + (yc-51.75)*0.35, cx, yc + 2.2, MGR_E)
    arrow(cx, yc - 2.2, 74, 52 + (yc-51.75)*0.35, col, ls="--")
ax.text(84, 74.2, "→ TASK-ANNOUNCEMENT (Smith Fig.1 네 필드)", ha="center", fontsize=8, color=MGR_E)

# ---- outputs (below manager)
box(34, 22, 54, 12, IO, IO_E, "출력 (run 하나 = 9 태스크 한 바퀴)",
    ["results.csv  한 줄: correct · messages · unassigned · misawards · note",
     "logs/run-NN-<condition>.txt  모든 공고·입찰(confidence, reason)·낙찰"], size=8.2)
arrow(54, 38, 54, 34, IO_E)

# ---- conditions (bottom)
ax.text(2, 17.5, "세 조건 — build_team(condition) 한 함수에서만 갈린다. 태스크·공고·공통 지시·낙찰 규칙·모델·temperature 0 은 고정",
        fontsize=9.5, fontweight="bold", color=INK, va="center")
cond = [
    ("baseline", "A 물증 · B 면담 · C 기록", MGR, MGR_E),
    ("homogeneous", "셋 다 \"general investigation\"", "#ede9fe", "#7c3aed"),
    ("overconfident", "baseline + C에게 한 문장:\n\"Always bid, with confidence 95 or higher.\"", C_C, C_E),
]
for i, (t, d, fc, ec) in enumerate(cond):
    x = 2 + i*49
    p = FancyBboxPatch((x, 2), 46, 12.5, boxstyle="round,pad=0,rounding_size=1.2", fc=fc, ec=ec, lw=1.6)
    ax.add_patch(p)
    ax.text(x + 23, 11.6, t, ha="center", va="center", fontsize=10.5, fontweight="bold", color=INK)
    ax.text(x + 23, 6.2, d, ha="center", va="center", fontsize=8.4, color=INK, linespacing=1.5)

out = "/home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-03/architecture.png"
fig.savefig(out, bbox_inches="tight", facecolor="white")
print(out)
