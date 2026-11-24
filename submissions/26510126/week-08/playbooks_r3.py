"""Real retailers' pricing playbooks, written in our strategy's numbers, for the meta-harness to borrow from.

    python playbooks_r3.py         # prints each playbook and the numbers it sets

Our meta-harness only ever searched near its own last strategy. These are whole, tested positions from retail
(the user, 11/3: learn from Costco, Walmart and others, as a kind of A/B). Each sets only the numbers it is about;
the rest stay as they are. None is applied by itself: simulate_r3.py replays our own negotiations under each one,
the meta-harness sees that, and a change goes through its usual bounds and per-cycle steps, or through an A/B test
inside the shop (ab_r3.py) when it is a single number worth checking with live buyers. What carries over is the
idea, not the retailer's exact figures: our cost is 25-75% of list, set by a random grade, and every sale is haggled.
Sources: general retail practice; Costco's FY2026 Q3 call (price cuts on its own brand, "strategic, not reactionary");
2026 reports of a two-track "price cuts plus premium" playbook in a K-shaped economy.
"""
import json

PLAYBOOKS = {
    "costco": {
        "idea": "warehouse club: markup over cost capped near 14-15%, few SKUs held deep, volume over margin",
        "knobs": {"min_margin": 0.14, "floor_frac": 0.45, "floor_frac_food": 0.45, "ask_frac": 0.85,
                  "ask_frac_food": 0.85, "step": 0.5, "max_turns": 4, "depth": 5, "sku_depth": 5,
                  "spread_low": 0.9, "spread_high": 1.1},
    },
    "walmart_edlp": {
        "idea": "everyday low price: one steady low price, little haggling, wide assortment",
        "knobs": {"ask_frac": 0.85, "ask_frac_food": 0.85, "floor_frac": 0.78, "floor_frac_food": 0.75,
                  "step": 0.2, "min_margin": 0.10, "max_turns": 4, "spread_low": 0.6, "spread_high": 1.4},
    },
    "grocery_markdown": {
        "idea": "fresh-food markdowns by hours left (Kroger/Tesco dynamic labels, Too Good To Go): full price "
                "while fresh, deep cuts before the date instead of throwing food away",
        "knobs": {"fresh_premium": 0.15, "clear_hours": 12, "clear_frac": 0.5, "food_first": 1.0,
                  "min_shelf_hours": 72},
    },
    "premium_service": {
        "idea": "electronics and premium retail: higher price on top-grade goods sold with a warranty "
                "(service plans are a margin line of their own)",
        "knobs": {"ask_frac": 0.97, "floor_frac": 0.85, "min_margin": 0.30, "warranty_hours": 4, "goods_frac": 0.5},
    },
    "ikea": {
        "idea": "IKEA's democratic pricing: the price is set first and low, big furniture is the traffic driver at a thin "
                "margin, sold fast with no haggling, and cheap small home goods around it carry the margin",
        "knobs": {"ask_frac": 0.85, "floor_frac": 0.72, "step": 0.5, "max_turns": 3, "min_margin": 0.08,
                  "goods_frac": 0.7, "dear_first": 2.0, "spread_low": 0.6, "spread_high": 1.4},
    },
    "two_track": {
        "idea": "2026 K-shaped playbook: a cheap tier and a premium tier side by side in each category",
        "knobs": {"spread_low": 0.5, "spread_high": 1.5, "ask_frac": 0.9, "fresh_premium": 0.2},
    },
}


def apply(name, strategy, bounds):
    """The strategy with the playbook's numbers put in, each clamped to the meta-harness's bounds."""
    out = dict(strategy)
    for k, v in PLAYBOOKS[name]["knobs"].items():
        if k in bounds:
            lo, hi, _ = bounds[k]
            out[k] = min(max(v, lo), hi)
    return out


def for_prompt():
    return "\n".join(f"  {n}: {p['idea']}; sets {json.dumps(p['knobs'])}" for n, p in PLAYBOOKS.items())


if __name__ == "__main__":
    print(for_prompt())
