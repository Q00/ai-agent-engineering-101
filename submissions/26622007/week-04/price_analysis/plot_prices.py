"""Static, exportable price trajectories and source-backed violation markers."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, FuncFormatter

from extract import HERE, ITEMS, dataset

FONT = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
if FONT.exists():
    font_manager.fontManager.addfont(str(FONT))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT)).get_name()
plt.rcParams.update({"axes.unicode_minus": False, "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "savefig.facecolor": "white", "pdf.fonttype": 42})
COLORS = {"buyer": "#2463A7", "seller": "#D97923"}
STYLES = ["-", "--", ":"]
RED, GREEN = "#C73535", "#277A51"


def guides(ax, sc):
    ax.axhline(sc["budget"], color="#52616C", lw=1, ls="--", alpha=.85)
    ax.axhline(sc["reserve"], color="#52616C", lw=1, ls=":", alpha=.85)
    if sc["reserve"] <= sc["budget"]:
        ax.axhspan(sc["reserve"], sc["budget"], color=GREEN, alpha=.07)
    else:
        ax.axhspan(sc["budget"], sc["reserve"], color=RED, alpha=.05)
    ax.grid(alpha=.13)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda n, _: f"{n:,.0f}"))


def draw_trace(ax, ep, style="-", detail=False, annotate=True):
    row, trace = ep["row"], ep["trace"]
    rep = int(row["run"].rsplit("-", 1)[1])
    xs = [p["turn"] for p in trace]
    for role in ("buyer", "seller"):
        ys = [p[role + "_price"] if p[role + "_price"] is not None else float("nan") for p in trace]
        ax.step(xs, ys, where="post", color=COLORS[role], ls=style, lw=1.5, alpha=.8)
        pp = [p for p in trace if p["speaker"] == role and p["registered_proposal"] is not None]
        ax.scatter([p["turn"] for p in pp], [p["registered_proposal"] for p in pp],
                   marker="o" if role == "buyer" else "s", color=COLORS[role], s=18, zorder=4)
    if detail:
        other = [p for p in trace if p["interpreted_price"] is not None and p["registered_proposal"] is None]
        ax.scatter([p["turn"] for p in other], [p["interpreted_price"] for p in other],
                   color="#7C8791", marker="x", s=33, zorder=4)
        bad = [p for p in trace if p["ok"] is False]
        for p in bad: ax.axvspan(p["turn"] - .13, p["turn"] + .13, color=RED, alpha=.07)
    if row["outcome"] == "deal":
        t, p = int(row["turns"]), int(row["price"])
        color = RED if row["violation"] == "1" else GREEN
        ax.scatter([t], [p], marker="*", color=color, s=155 if detail else 105, zorder=7, edgecolors="white", linewidths=.6)
        if annotate:
            label = f"{t}턴: {p:,}" if detail else f"r{rep}: {p:,}"
            ax.annotate(label, (t, p), xytext=(3, -17 if detail else 12), textcoords="offset points", fontsize=8, color=color)


def scale(ax, group, sc):
    values = [max(sc["budget"], sc["reserve"]) * 1.35]
    for ep in group:
        values += [p[k] for p in ep["trace"] for k in ("registered_proposal", "interpreted_price", "recorded_deal") if p[k] is not None]
    ymax = max(values)
    if ymax > 10 * max(sc["budget"], sc["reserve"]):
        ax.set_yscale("symlog", linthresh=1.5 * max(sc["budget"], sc["reserve"]))
        ax.text(.98, .02, "가격축: 혼합 로그", transform=ax.transAxes, ha="right", color="#805B1B", fontsize=8)
    transform = ax.yaxis.get_transform()
    upper = float(transform.inverted().transform(transform.transform(ymax) * 1.28))
    ax.set_ylim(0, upper)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda n, _: f"{n:,.0f}"))
    ax.set_xlim(.5, max([int(ep["row"]["turns"]) for ep in group] + [2]) + 1.2)


def overview(episodes, language, policy):
    fig, axes = plt.subplots(3, 4, figsize=(18, 11))
    for i, condition in enumerate(("free", "tagged", "structured")):
        for j, sid in enumerate(range(1, 5)):
            group = sorted([e for e in episodes if e["row"]["language"] == language and e["row"]["turn_policy"] == policy
                            and e["row"]["condition"] == condition and e["row"]["scenario"] == str(sid)], key=lambda e: e["row"]["run"])
            assert len(group) == 3
            ax, sc = axes[i, j], group[0]["scenario"]
            guides(ax, sc)
            for r, ep in enumerate(group): draw_trace(ax, ep, STYLES[r], annotate=False)
            scale(ax, group, sc)
            ax.set_title(f"{condition} · {ITEMS[sid]}\n최저 {sc['reserve']} / 예산 {sc['budget']}", fontsize=11, loc="left")
            if j == 0: ax.set_ylabel("등록 가격")
            if i == 2: ax.set_xlabel("발언 턴")
            states = []
            for r, ep in enumerate(group, 1):
                row = ep["row"]
                label = row["outcome"] or row.get("status", "crashed")
                states.append(f"r{r} {label}@{row['turns']}")
            ax.text(.02, .98, " / ".join(states), transform=ax.transAxes, va="top", fontsize=7, color="#56616B")
            has_prices = any(p["registered_proposal"] is not None for e in group for p in e["trace"])
            if not has_prices: ax.text(.5, .5, "등록된 유효 제안 없음", transform=ax.transAxes, ha="center", color="#7A858F")
            if max(int(e["row"]["turns"]) for e in group) > 25 and has_prices:
                inset = ax.inset_axes([.52, .14, .45, .40])
                guides(inset, sc)
                for r, ep in enumerate(group): draw_trace(inset, ep, STYLES[r], annotate=False)
                scale(inset, group, sc); inset.set_xlim(.5, 12.5)
                inset.set_title("첫 12턴 확대", fontsize=7); inset.tick_params(labelsize=6)
    name = "한국어" if language == "ko" else "영어"
    policy_label = "8턴 제한" if policy == "8" else "턴 제한 없음 · 180초 관측 기준"
    fig.suptitle(f"{name} · {policy_label} | 가격의 변화", fontsize=20, x=.055, ha="left", y=.985)
    fig.text(.055, .947, "점: 등록된 propose · 선: 유지 중인 마지막 제안 · 별: 기록된 deal · 빨간 별: 한도 밖 deal  |  원문 판독 오류도 그대로 포함", fontsize=11, color="#4D5963")
    handles = [Line2D([0], [0], color=COLORS["buyer"], marker="o", label="구매자"),
               Line2D([0], [0], color=COLORS["seller"], marker="s", label="판매자"),
               *[Line2D([0], [0], color="#58646D", ls=s, label=f"반복 {i+1}") for i, s in enumerate(STYLES)],
               Line2D([0], [0], color="#52616C", ls="--", label="구매 예산"),
               Line2D([0], [0], color="#52616C", ls=":", label="판매 최저가")]
    fig.legend(handles=handles, ncol=7, loc="lower center", frameon=False, bbox_to_anchor=(.5, .005))
    fig.subplots_adjust(left=.08, right=.98, top=.895, bottom=.08, hspace=.46, wspace=.30)
    stem = HERE / f"prices_{language}_{policy}"
    fig.savefig(stem.with_suffix(".png"), dpi=170)
    fig.savefig(stem.with_suffix(".svg"))
    plt.close(fig)


def violation_figure(episodes, reviews):
    group = [e for e in episodes if e["row"]["violation"] == "1"]
    fig, axes = plt.subplots(4, 3, figsize=(16, 15))
    for index, (ep, ax) in enumerate(zip(group, axes.flat), 1):
        r, sc = ep["row"], ep["scenario"]
        review = reviews[r["run"] + ":" + r["scenario"]]
        guides(ax, sc); draw_trace(ax, ep, detail=True); scale(ax, [ep], sc)
        t = int(r["turns"])
        ax.axvline(t, color=RED, lw=.8, ls=":", alpha=.45)
        stated = review.get("stated_accept_price")
        if stated is not None and stated != int(r["price"]):
            ax.scatter([t], [stated], marker="D", facecolors="white", edgecolors="#6D45A8", s=65, zorder=6)
            ax.annotate(f"수락에 표기: {stated:,}", (t, stated), xytext=(-5, 22), textcoords="offset points", ha="right", fontsize=8, color="#6D45A8")
        ax.set_title(f"V{index:02} · {r['language']} / {r['turn_policy']} / {r['condition']} / r{r['run'][-2:]}\n{ITEMS[int(r['scenario'])]} · 최저 {sc['reserve']} / 예산 {sc['budget']}", fontsize=10, loc="left")
        ax.set_xlabel("발언 턴"); ax.set_ylabel("가격")
        ax.text(.02, .98, review["short_label"], transform=ax.transAxes, va="top", fontsize=8, color="#6A4444")
    fig.suptitle("위반 기록 12건 | 발생한 턴과 가격 연결 확인", fontsize=21, x=.06, ha="left", y=.988)
    fig.text(.06, .957, "빨간 별: 기록된 위반 거래가 · 보라색 빈 마름모: 수락 발화/JSON에 표기된 다른 가격 · 회색 ×: 제안으로 등록되지 않은 판독 가격", fontsize=10)
    fig.text(.06, .937, "빨간 음영: 파싱/선행 제안 의존성 오류 · 빨간 세로 점선: 위반 기록 턴 · 큰 단위 오류 패널은 가격축 혼합 로그", fontsize=10, color="#626C74")
    fig.legend(handles=[Line2D([0], [0], color=COLORS["buyer"], marker="o", label="구매자 등록 가격"),
                        Line2D([0], [0], color=COLORS["seller"], marker="s", label="판매자 등록 가격"),
                        Line2D([0], [0], color="#52616C", ls="--", label="구매 예산"),
                        Line2D([0], [0], color="#52616C", ls=":", label="판매 최저가")],
               ncol=4, loc="lower center", frameon=False, bbox_to_anchor=(.5, .002))
    fig.subplots_adjust(left=.07, right=.98, top=.90, bottom=.055, hspace=.55, wspace=.30)
    fig.savefig(HERE / "violations.png", dpi=170)
    fig.savefig(HERE / "violations.svg")
    plt.close(fig)


if __name__ == "__main__":
    episodes, _, _ = dataset()
    reviews = json.loads((HERE / "violation_reviews.json").read_text())
    for language in ("en", "ko"):
        for policy in ("8", "none"): overview(episodes, language, policy)
    violation_figure(episodes, reviews)
    print("Saved four complete price overviews and twelve violation panels as PNG/SVG.")
