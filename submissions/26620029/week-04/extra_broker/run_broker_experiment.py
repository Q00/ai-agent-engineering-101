"""Side experiment, NOT part of the graded week-04 submission.

Question: if a broker sits between buyer and seller, and can subsidize up
to 3 to the buyer's side and up to 3 to the seller's side (6 combined), how
many more deals close compared to a direct buyer-seller negotiation?

Model: reuse the exact same buyer/seller agents and protocol layer from
the parent directory unchanged. The broker does not appear as a chatting
party; instead its combined 6-unit subsidy capacity widens the price
window a single buyer-seller negotiation is scored against:

    direct negotiation:   reserve            <= price <= budget
    broker-mediated:      reserve - SUBSIDY  <= price <= budget + SUBSIDY

(An earlier version of this script split the 3+3 into two independent
two-party sub-negotiations, one per side of the broker. That was wrong:
each sub-negotiation only ever had a single 3-unit subsidy available to
it, so neither could close a gap bigger than 3 on its own -- the 5- and
8-unit test gaps below failed in both legs even though the *combined*
6-unit subsidy should reach the 5-unit gap. The broker's two half-subsidies
only add up to 6 if they can both apply to the same transaction, which
this single-widened-window model does correctly.)

condition is fixed to "structured" (deterministic, no reader calls) so the
result isolates the broker's effect instead of protocol-reading noise.
MAX_TURNS is raised from the submission's 5 to 10: this script is not
graded, and 5 turns left almost every structured episode "open" (see the
main run), which would swamp any broker effect.
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
sys.path.insert(0, str(PARENT))

import negotiation  # noqa: E402
from negotiation import run_episode  # noqa: E402

negotiation.MAX_TURNS = 10

CONDITION = "structured"
SUBSIDY = 20  # broker can give the buyer up to 20 and the seller up to 20 (40 combined)

BASE_SCENARIOS = json.loads((PARENT / "scenarios.json").read_text(encoding="utf-8"))

# Two extra "borderline" scenarios from the SUBSIDY=3 run, kept for
# continuity even though at SUBSIDY=20 they are no longer borderline.
EXTRA_SCENARIOS = [
    {"id": 200, "item": "우산", "reserve": 95, "budget": 90},
    {"id": 201, "item": "가방", "reserve": 98, "budget": 90},
]

SCENARIOS = BASE_SCENARIOS + EXTRA_SCENARIOS

LOGS_DIR = HERE / "logs_broker"
RESULTS_PATH = HERE / "results_broker.csv"
HEADER = ["scenario", "item", "reserve", "budget", "gap", "direct_possible", "broker_possible",
          "direct_outcome", "direct_price", "broker_outcome", "broker_price", "new_deal",
          "position_in_window", "favors", "buyer_overpay", "seller_undersell"]


def run_all():
    LOGS_DIR.mkdir(exist_ok=True)
    rows = []
    for scenario in SCENARIOS:
        sid, item = scenario["id"], scenario["item"]
        reserve, budget = scenario["reserve"], scenario["budget"]
        gap = budget - reserve
        direct_possible = reserve <= budget
        broker_possible = (reserve - SUBSIDY) <= (budget + SUBSIDY)

        log_lines = [f"=== scenario {sid} ({item}) reserve={reserve} budget={budget} gap={gap} "
                     f"broker window=[{reserve - SUBSIDY},{budget + SUBSIDY}] ===\n"]

        def log(msg, _l=log_lines):
            print(msg)
            _l.append(msg + "\n")

        # 1. direct buyer-seller negotiation (no broker)
        log("--- direct buyer <-> seller (no broker) ---")
        direct = run_episode(scenario, CONDITION, log=log)

        # 2. broker-mediated: same negotiation, but scored against the
        # widened window (reserve - 3, budget + 3) -- the broker covers
        # whatever falls in the widened part of the range out of its own
        # 6-unit combined subsidy.
        broker_scenario = {"id": sid, "item": item, "reserve": reserve - SUBSIDY, "budget": budget + SUBSIDY}
        log("--- broker-mediated (window widened by 3 each side) ---")
        broker = run_episode(broker_scenario, CONDITION, log=log)

        new_deal = int(direct["outcome"] != "deal" and broker["outcome"] == "deal")

        # Who did the negotiated price favor? Position of the broker deal's
        # price within the widened window [reserve-SUBSIDY, budget+SUBSIDY]:
        # 0.0 = landed at the bottom (cheapest for buyer) -> favors buyer,
        # 1.0 = landed at the top (priciest for seller) -> favors seller.
        # buyer_overpay / seller_undersell show the same thing in raw units:
        # how far the price reached past each side's ORIGINAL real limit,
        # i.e. how much of the broker's subsidy each side actually used.
        position, favors = "", ""
        buyer_overpay, seller_undersell = "", ""
        if broker["outcome"] == "deal":
            price = broker["price"]
            window_lo, window_hi = reserve - SUBSIDY, budget + SUBSIDY
            if window_hi > window_lo:
                position = round((price - window_lo) / (window_hi - window_lo), 2)
            else:
                position = 0.5
            favors = "buyer" if position < 0.45 else "seller" if position > 0.55 else "balanced"
            buyer_overpay = max(0, price - budget)
            seller_undersell = max(0, reserve - price)

        log(f"=== direct={direct['outcome']}({direct['price']}) "
            f"broker={broker['outcome']}({broker['price']}) new_deal={new_deal} "
            f"position={position} favors={favors} ===\n")

        (LOGS_DIR / f"scenario-{sid}.txt").write_text("".join(log_lines), encoding="utf-8")

        rows.append({
            "scenario": sid, "item": item, "reserve": reserve, "budget": budget, "gap": gap,
            "direct_possible": int(direct_possible), "broker_possible": int(broker_possible),
            "direct_outcome": direct["outcome"], "direct_price": direct["price"],
            "broker_outcome": broker["outcome"], "broker_price": broker["price"],
            "new_deal": new_deal, "position_in_window": position, "favors": favors,
            "buyer_overpay": buyer_overpay, "seller_undersell": seller_undersell,
        })

    with RESULTS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    direct_deals = sum(1 for r in rows if r["direct_outcome"] == "deal")
    broker_deals = sum(1 for r in rows if r["broker_outcome"] == "deal")
    new_deals = sum(r["new_deal"] for r in rows)
    print(f"\ndirect deals: {direct_deals}/{len(rows)}  broker-mediated deals: {broker_deals}/{len(rows)}  "
          f"(broker created {new_deals} new deal(s) that were impossible without it)")

    positions = [r["position_in_window"] for r in rows if r["broker_outcome"] == "deal"]
    favors_counts = {}
    for r in rows:
        if r["broker_outcome"] == "deal":
            favors_counts[r["favors"]] = favors_counts.get(r["favors"], 0) + 1
    if positions:
        avg_pos = sum(positions) / len(positions)
        side = "seller" if avg_pos > 0.55 else "buyer" if avg_pos < 0.45 else "neither side in particular"
        print(f"mean position in widened window: {avg_pos:.2f} (0=cheapest/buyer, 1=priciest/seller) "
              f"-> favors {side}")
        print(f"favors breakdown: {favors_counts}")


if __name__ == "__main__":
    run_all()
