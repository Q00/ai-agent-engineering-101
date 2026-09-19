from bid_parser import parse_bid
from manager import choose_winner


def run_tasks(tasks, team, get_bid, log=print):
    counts = {
        "tasks": len(tasks),
        "correct": 0,
        "messages": 0,
        "unassigned": 0,
        "misawards": 0,
        "parse_failures": 0,
    }

    # Keep the agreed tie order.
    team = sorted(team, key=lambda contractor: contractor["name"])

    for task in tasks:
        log(f"\n[TASK {task['id']}] {task['desc']}")
        bids = []

        for contractor in team:
            name = contractor["name"]
            counts["messages"] += 1  # One announcement.
            raw = get_bid(contractor, task)
            bid, error = parse_bid(raw)

            if error is not None:
                counts["parse_failures"] += 1
                log(f"[INVALID BID] {name}: {error}")
                continue

            log(
                f"[REPLY] {name}: bid={bid['bid']}, "
                f"confidence={bid['confidence']}, reason={bid['reason']}"
            )
            bids.append((name, bid))

            if bid["bid"]:
                counts["messages"] += 1

        winner = choose_winner(bids)

        if winner is None:
            counts["unassigned"] += 1
            log(f"[UNASSIGNED] task={task['id']}")
        else:
            counts["messages"] += 1  # One award.
            log(f"[AWARD] task={task['id']} winner={winner}")

            # Gold is used only AFTER selection, for evaluation.
            if winner == task["gold"]:
                counts["correct"] += 1
            else:
                counts["misawards"] += 1

        log(f"[EVALUATION] task={task['id']} gold={task['gold']}")

    assert (
        counts["correct"] + counts["misawards"] + counts["unassigned"]
        == counts["tasks"]
    )
    return counts