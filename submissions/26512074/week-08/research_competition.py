"""Fast sensitivity analysis, explicitly synthetic rather than leaderboard proof."""
import itertools
import json
from pathlib import Path

RESERVE = 100
ARMS = [0, 1, 3, 5, 15, 25, 40]
PROFILES = ["floor_patient", "ask_110", "ask_125", "ask_150", "one_shot_110", "first_floor_then_walk"]


def trade(markup, profile, defensive=False):
    ask, rounds = RESERVE + markup, 0
    for turn in range(5):
        limit = {"ask_110": 110, "ask_125": 125, "ask_150": 150, "one_shot_110": 110}.get(profile, 100)
        if profile == "one_shot_110" and ask > limit:
            return 0, 0
        offer = ask if profile.startswith("ask_") and ask <= limit else min(limit, 100)
        if profile == "one_shot_110":
            offer = ask
        minimum = max(1, markup // 3) if defensive and markup and turn < 2 else 0
        if offer >= RESERVE + minimum:
            return 1, offer - RESERVE
        if profile == "first_floor_then_walk":
            return 0, 0
        ask = max(RESERVE + minimum, ask - 5)
    return 0, 0


def run():
    mixtures = [weights for weights in itertools.product(range(5), repeat=6) if sum(weights) == 4]
    outputs = []
    for weights in mixtures:
        def profit(arm, defensive):
            return sum(w * trade(arm, p, defensive)[1] / 4 for w, p in zip(weights, PROFILES))
        old = profit(15, False)
        candidates = {str(arm): profit(arm, True) for arm in ARMS}
        best = max(ARMS, key=lambda arm: (candidates[str(arm)], -arm))
        outputs.append({"mixture": dict(zip(PROFILES, weights)), "old_fixed_15": old,
                        "candidate_profit_per_opportunity": candidates, "oracle_best_arm": best,
                        "oracle_best_profit": candidates[str(best)]})
    result = {"kind": "synthetic_sensitivity_only", "mixtures": len(mixtures), "arms": len(ARMS),
              "scenarios": len(mixtures) * len(ARMS), "profiles": PROFILES,
              "oracle_better_than_old": sum(r["oracle_best_profit"] > r["old_fixed_15"] for r in outputs),
              "oracle_equal_to_old": sum(r["oracle_best_profit"] == r["old_fixed_15"] for r in outputs),
              "warning": "Oracle knows the synthetic mix. Online learner does not. No real profit or first-place result is established.",
              "cases": outputs}
    target = Path(__file__).with_name("logs") / "competition-sensitivity.json"
    if target.exists():
        raise SystemExit("Preserve the existing analysis instead of overwriting it.")
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}))


if __name__ == "__main__":
    run()
