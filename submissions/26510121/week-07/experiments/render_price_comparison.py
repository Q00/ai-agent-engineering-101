"""Render the observed comparison for the Markdown submission (system matplotlib)."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison", type=Path)
    args = parser.parse_args()
    data = json.loads(args.comparison.read_text(encoding="utf-8"))
    phases = [data["baseline"], data["candidate"]]
    labels = ["Prior margin", "Published minimum"]
    colors = ["#61758d", "#00856a"]
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.8), constrained_layout=True)
    for ax, key, title, fmt in zip(axes,
          ["window_deals", "window_share_percent", "window_mean_price"],
          ["Counted sales", "Share of market sales (%)", "Mean sale price (price units)"],
          [".0f", ".2f", ".2f"]):
        values = [phase[key] for phase in phases]
        plotted = [value if value is not None else 0 for value in values]
        bars = ax.bar(labels, plotted, color=colors, width=0.55)
        for bar, value in zip(bars, values):
            ax.annotate("N/A" if value is None else format(value, fmt),
                        (bar.get_x() + bar.get_width()/2, bar.get_height()),
                        xytext=(0, 5), textcoords="offset points", ha="center", fontsize=10)
        ax.set_title(title, fontsize=11)
        ax.set_ylim(0, max(plotted + [1]) * 1.25)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="x", labelsize=9)
        ax.grid(axis="y", alpha=.15)
        ax.set_axisbelow(True)
    fig.suptitle("Live R2: equal 3-minute policy windows, one shop", fontsize=13)
    fig.savefig(args.comparison.with_name("price-comparison.png"), dpi=160)
    plt.close(fig)
    print(args.comparison.with_name("price-comparison.png"))


if __name__ == "__main__":
    main()
