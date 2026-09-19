"""Week 03 — manager: broadcast one task, collect bids, award to one contractor.

Step 3 scope: just the award decision for one task. The full round (all
tasks, metrics, conditions) comes next once this piece is verified.
"""
from contractor import Contractor, bid
from model import Meter


def collect_bids(team, task_id, desc, meter):
    """Ask every contractor in turn; keep only real bids (bid is True)."""
    accepted = []
    for c in team:
        result = bid(c, task_id, desc, meter)
        if result is None:
            print(f"  [bid] {c.name}: parse failed, treated as no bid")
            continue
        print(f"  [bid] {c.name}: bid={result['bid']} confidence={result['confidence']}")
        if result["bid"] is True:
            accepted.append((c.name, result["confidence"]))
    return accepted


def award(accepted):
    """Highest confidence wins; a tie goes to whoever appears first (i.e.
    answered first, since contractors are asked in team order)."""
    if not accepted:
        return None  # unassigned: nobody bid
    best_name, best_confidence = accepted[0]
    for name, confidence in accepted[1:]:
        if confidence > best_confidence:
            best_name, best_confidence = name, confidence
    return best_name


if __name__ == "__main__":
    team = [
        Contractor(name="A", skill="arithmetic"),
        Contractor(name="B", skill="writing"),
        Contractor(name="C", skill="coding"),
    ]
    m = Meter()
    desc = "Compute 137 * 249 and return only the number."
    print("[announce] task 1 ->", [c.name for c in team])
    accepted = collect_bids(team, 1, desc, m)
    winner = award(accepted)
    print(f"[award] winner={winner}  (gold=A)")
