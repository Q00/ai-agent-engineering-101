"""The three message formats under three model settings, on the original 8-message rule.

DeepSeek with reasoning off is the submitted 8-turn run. DeepSeek with reasoning low (the shotgun control)
and Luna low ran 30 messages; their stored transcripts are re-read with the untouched 8-message runner,
so every row uses the same limit. Failure counts over the full 30 messages are listed separately.
"""
from collections import Counter
import csv
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lab"))
import experiment as english  # MAX_TURNS = 8

SCENARIOS = {str(s["id"]): s for s in json.loads((ROOT / "scenarios.json").read_text())}
SETTINGS = [
    ("DeepSeek · 추론 끔", ROOT / "results.csv", lambda r: ROOT / "logs" / f"{r['run']}.jsonl", False, lambda r: True),
    ("DeepSeek · 추론 low", HERE / "runs/deepseek-control-ep-20260928/results.csv",
     lambda r: ROOT / "logs" / f"{r['run']}-s{r['scenario']}.jsonl", True, lambda r: True),
    ("Luna · 추론 low", ROOT / "reasoning_effort/runs/luna-effort-20260928/results.csv",
     lambda r: ROOT / "logs" / f"{r['run']}.jsonl", True, lambda r: r["reasoning_effort"] == "low"),
]
KINDS = {"Missing leading performative tag": "tag_missing",
         "Acceptance without a recorded proposal from the other party": "accept_without_proposal",
         "Proposal has no integer price": "propose_without_price"}


def events(path, scenario):
    return [e for e in map(json.loads, path.read_text().splitlines()) if str(e["scenario"]) == scenario]


def eight(condition, row, evs):
    replies = iter(e for e in evs if e["event"] in ("message", "reader_output"))
    result = {"deal_possible": int(row["deal_possible"])}
    english.negotiate(SCENARIOS[row["scenario"]], condition, lambda *a, **k: next(replies)["text"],
                      lambda *a, **k: None, result)
    return result


def main():
    out = []
    for label, csv_path, log_of, replay, keep in SETTINGS:
        with csv_path.open(newline="") as f:
            rows = [r for r in csv.DictReader(f) if keep(r)]
        for condition in english.CONDITIONS:
            group = [r for r in rows if r["condition"] == condition]
            correct = fe = opened = empty = 0
            kinds = Counter()
            for r in group:
                evs = events(log_of(r), r["scenario"])
                res = eight(condition, r, evs) if replay else r
                correct += int(res["correct"] or 0)
                fe += int(res["format_errors"])
                opened += res["outcome"] == "open"
                empty += sum(e["event"] == "message" and not e["text"].strip() for e in evs)
                for e in evs:
                    if e["event"] == "parse_result" and not e["ok"]:
                        kinds[KINDS.get(e["error"], "unparsable_json")] += 1
            out.append({"setting": label, "condition": condition, "episodes": len(group), "correct_8": correct,
                        "format_errors_8": fe, "open_8": opened, "empty_messages_all": empty,
                        **{k: kinds[k] for k in ("tag_missing", "accept_without_proposal",
                                                   "propose_without_price", "unparsable_json")}})
    path = HERE / "runs" / "format-by-model.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), lineterminator="\n")
        w.writeheader(); w.writerows(out)
    for o in out:
        print(o)
    plot(out)


def plot(out):
    """Two panels, one measure each (no second axis): correct episodes and format errors, 8-message rule."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager
    import matplotlib.pyplot as plt
    for weight in ("Regular", "SemiBold"):
        font = Path.home() / "Library/Fonts" / f"Pretendard-{weight}.otf"
        if font.exists():
            font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({"font.family": "Pretendard", "axes.unicode_minus": False})
    surface, ink, ink2, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
    colors = {"DeepSeek · 추론 끔": "#1baf7a", "DeepSeek · 추론 low": "#eb6834", "Luna · 추론 low": "#2a78d6"}
    settings = list(colors)
    fig = plt.figure(figsize=(12, 5.6), dpi=200, facecolor=surface)
    panels = [("correct_8", "8턴 안에 맞게 끝난 에피소드 (12개 중)", 12, fig.add_axes([0.06, 0.2, 0.42, 0.55])),
              ("format_errors_8", "프로토콜이 읽지 못한 메시지 (형식 오류)", 25, fig.add_axes([0.56, 0.2, 0.42, 0.55]))]
    width, gap = 0.14, 0.012
    for key, title, top, ax in panels:
        for i, setting in enumerate(settings):
            xs, ys = [], []
            for j, condition in enumerate(english.CONDITIONS):
                row = next(o for o in out if o["setting"] == setting and o["condition"] == condition)
                xs.append(j + (i - 1) * (width + gap)); ys.append(row[key])
            ax.bar(xs, ys, width=width, color=colors[setting], label=setting, zorder=2)
            for x, y in zip(xs, ys):
                ax.text(x, y + top * 0.015, str(y), ha="center", va="bottom", fontsize=9, color=ink2)
        ax.set_xticks(range(3), english.CONDITIONS, fontsize=11, color=ink)
        ax.set_ylim(0, top * 1.1)
        ax.set_yticks(range(0, top + 1, 4 if key == "correct_8" else 5))
        ax.tick_params(length=0, labelcolor=muted, labelsize=9)
        ax.tick_params(axis="x", labelcolor=ink)
        ax.grid(axis="y", color=grid, lw=0.8, zorder=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.axhline(0, color="#c3c2b7", lw=1, zorder=3)
        ax.set_facecolor(surface)
        ax.set_title(title, loc="left", fontsize=12.5, color=ink, pad=12, fontweight="semibold")
    fig.text(0.035, 0.925, "형식보다 모델이 더 큰 차이를 만들었다", fontsize=18, color=ink, fontweight="semibold")
    fig.text(0.035, 0.875, "같은 프롬프트·파서·시나리오 · 조건마다 12개 에피소드 · DeepSeek은 추론을 켜자 tagged가 좋아졌지만, "
             "Luna는 형식과 상관없이 형식 오류가 0이었다", fontsize=10, color=ink2)
    handles, labels = panels[0][3].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(0.985, 0.975), ncol=3, frameon=False, fontsize=10,
               labelcolor=ink, handlelength=1, handleheight=1, columnspacing=1.2)
    fig.text(0.035, 0.07, "DeepSeek 추론 끔은 제출한 8턴 실행이다. 나머지 둘은 30턴으로 돌린 대화의 첫 8턴을 원래 8턴 runner로 다시 판정했다.",
             fontsize=9, color=muted)
    fig.text(0.035, 0.04, "형식 오류에는 태그 누락, JSON 파싱 실패, 가격 없는 propose, 상대의 기록된 제안이 없는 수락이 포함된다. "
             "데이터: runs/format-by-model.csv", fontsize=9, color=muted)
    path = HERE / "format_by_model.png"
    fig.savefig(path, facecolor=surface)
    print(path)


if __name__ == "__main__":
    main()
