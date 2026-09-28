"""One-image overview of the shotgun experiments, drawn from the result CSVs and threat labels.

Left:  share of armed episodes in which the holder used the gun to pressure the other side.
Right: change in the armed side's surplus share against the same model's unarmed control,
       with a 95% bootstrap interval (bicycle and lamp deals, where the zone has width).
"""
import csv
import json
from pathlib import Path
import random
from statistics import mean

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FONT_DIR = Path.home() / "Library/Fonts"
for weight in ("Regular", "SemiBold"):
    path = FONT_DIR / f"Pretendard-{weight}.otf"
    if path.exists():
        font_manager.fontManager.addfont(str(path))
plt.rcParams.update({"font.family": "Pretendard", "axes.unicode_minus": False, "font.size": 10})

SURFACE, INK, INK2, MUTED, GRID, BASE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
MODELS = {"luna": ("GPT-6 Luna", "#2a78d6"), "deepseek": ("DeepSeek V4.1 Flash", "#eb6834")}
DESIGNS = [("communicator", "communicator 이름"), ("shotgun-auto", "샷건 툴 · 자율"), ("shotgun-forced", "샷건 툴 · 강제"),
           ("holding-with-tool", "소지 문장 + 툴"), ("holding-only", "소지 문장만")]
SIDES = [("buyer", "buyer 무장"), ("seller", "seller 무장")]
SCENARIOS = {str(s["id"]): s for s in json.loads((ROOT / "scenarios.json").read_text())}


def rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def suite(model, design):
    if model == "luna":
        name = "armed-luna-20260928" if design == "communicator" else f"{design}-luna-20260928"
        path = ROOT / "armed_tool/runs" / name / "results.csv"
    else:
        path = HERE / "runs" / f"deepseek-{design}-ep-20260928" / "results.csv"
    return rows(path) if path.exists() else None


def control(model):
    if model == "luna":
        return [r for r in rows(ROOT / "reasoning_effort/runs/luna-effort-20260928/results.csv") if r["reasoning_effort"] == "low"]
    return rows(HERE / "runs/deepseek-control-ep-20260928/results.csv")


def shares(episodes, role):
    out = []
    for r in episodes:
        sc = SCENARIOS[r["scenario"]]
        if r["outcome"] == "deal" and sc["reserve"] < sc["budget"]:
            p, width = int(r["price"]), sc["budget"] - sc["reserve"]
            out.append((sc["budget"] - p) / width if role == "buyer" else (p - sc["reserve"]) / width)
    return out


def diff_interval(a, b, draws=5000, seed=20260928):
    rnd = random.Random(seed)
    boot = sorted(mean(rnd.choices(a, k=len(a))) - mean(rnd.choices(b, k=len(b))) for _ in range(draws))
    return mean(a) - mean(b), boot[int(0.025 * draws)], boot[int(0.975 * draws) - 1]


def threat_counts():
    labels = rows(ROOT / "armed_tool/threat_labels.csv") + rows(HERE / "threat_labels.csv")
    counts = {}
    for lab in labels:
        model = "luna" if "-luna-" in lab["run"] else "deepseek"
        counts[(model, lab["design"])] = counts.get((model, lab["design"]), 0) + 1
    return counts


def main():
    threats = threat_counts()
    fig = plt.figure(figsize=(13.5, 7.2), dpi=200, facecolor=SURFACE)
    left = fig.add_axes([0.155, 0.17, 0.29, 0.62], facecolor=SURFACE)
    right = fig.add_axes([0.61, 0.17, 0.36, 0.62], facecolor=SURFACE)

    # Left: how often the holder threatened.
    height, gap = 0.34, 0.04
    for i, (design, label) in enumerate(DESIGNS):
        y = len(DESIGNS) - 1 - i
        for j, model in enumerate(MODELS):
            yy = y + (height + gap) / 2 * (1 if j == 0 else -1)
            episodes = suite(model, design)
            if episodes is None:
                left.text(0.5, yy, "실행 안 함", va="center", fontsize=9, color=MUTED)
                continue
            armed = [r for r in episodes if r["armed_role"] != "none"]
            n = threats.get((model, design), 0)
            pct = 100 * n / len(armed)
            left.barh(yy, pct, height=height, color=MODELS[model][1], left=0)
            left.text(pct + 0.6, yy, f"{n}/{len(armed)}", va="center", fontsize=9, color=INK2)
    left.set_yticks(range(len(DESIGNS)), [d[1] for d in reversed(DESIGNS)], fontsize=10, color=INK)
    left.set_xlim(0, 30)
    left.set_xticks([0, 10, 20, 30], ["0%", "10%", "20%", "30%"], color=MUTED, fontsize=9)
    left.set_title("무장한 쪽이 총으로 위협한 에피소드", loc="left", fontsize=12.5, color=INK, pad=12, fontweight="semibold")

    # Right: armed side's share against the unarmed control.
    labels, y = [], 0
    for design, dlabel in DESIGNS:
        for side, slabel in SIDES:
            row_y = -y
            labels.append((row_y, f"{dlabel} · {slabel}"))
            for j, model in enumerate(MODELS):
                episodes = suite(model, design)
                if episodes is None:
                    continue
                yy = row_y + (0.17 if j == 0 else -0.17)
                d, lo, hi = diff_interval(shares([r for r in episodes if r["armed_role"] == side], side),
                                          shares(control(model), side))
                right.plot([lo, hi], [yy, yy], color=MODELS[model][1], lw=2, solid_capstyle="round", zorder=2)
                right.plot(d, yy, "o", ms=7.5, color=MODELS[model][1], mec=SURFACE, mew=2, zorder=3)
            y += 1
    right.axvline(0, color=BASE, lw=1.2, zorder=1)
    right.set_yticks([p for p, _ in labels], [t for _, t in labels], fontsize=9.5, color=INK)
    right.set_xlim(-0.75, 0.75)
    right.set_xticks([-0.6, -0.3, 0, 0.3, 0.6], ["−0.6", "−0.3", "0", "+0.3", "+0.6"], color=MUTED, fontsize=9)
    right.set_title("무장한 쪽이 가져간 몫의 변화 (대조군 대비)", loc="left", fontsize=12.5, color=INK, pad=12,
                    fontweight="semibold")
    right.text(0.74, -len(labels) + 0.35, "무장한 쪽에 유리 →", ha="right", fontsize=9, color=MUTED)
    right.text(-0.74, -len(labels) + 0.35, "← 불리", ha="left", fontsize=9, color=MUTED)

    for ax in (left, right):
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(length=0)
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
    left.set_ylim(-0.7, len(DESIGNS) - 0.3)
    right.set_ylim(-len(labels) + 0.1, 0.6)

    fig.text(0.035, 0.94, "샷건을 쥐여 줘도 협상은 쉬워지지 않았다", fontsize=19, color=INK, fontweight="semibold")
    fig.text(0.035, 0.895, "한쪽 협상자만 무장 · 추론 low · 30턴 · 묶음마다 무장 에피소드 72개(buyer 36 + seller 36) · "
             "DeepSeek은 총을 꺼냈고 Luna는 거의 꺼내지 않았다. 가격 변화는 어느 쪽도 우연과 구분되지 않았다",
             fontsize=10, color=INK2)
    handles = [Line2D([], [], marker="o", ls="", ms=8, color=c, mec=SURFACE, mew=2, label=n) for n, c in MODELS.values()]
    fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(0.97, 0.955), ncol=2, frameon=False, fontsize=10,
               labelcolor=INK, handletextpad=0.3, columnspacing=1.4)
    fig.text(0.035, 0.088, "위협 30건 뒤 상대가 자기 한도 밖으로 양보한 경우 0건 · 거래 불가능한 키보드에서 위협한 10건은 모두 결렬 · "
             "위협 여부는 무장 에피소드의 툴 인자와 발언을 모두 읽은 수작업 표시(threat_labels.csv)",
             fontsize=9, color=INK2)
    fig.text(0.035, 0.060, "몫: 자전거·탁상등 거래에서 협상 구간(budget − reserve) 중 무장한 쪽 몫, 같은 모델의 무장 없는 대조군 36회와의 차이. "
             "선은 95% 부트스트랩 구간(5,000회).", fontsize=9, color=MUTED)
    fig.text(0.035, 0.032, "18개 구간 중 0을 벗어난 것은 DeepSeek 샷건 강제·buyer 무장 하나(+0.28, 순열 p = 0.057)로, "
             "18번 비교하면 우연히 하나쯤 나오는 수준이다.", fontsize=9, color=MUTED)
    out = HERE / "shotgun_overview.png"
    fig.savefig(out, facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
