from dataclasses import dataclass

from contractor import Contractor, bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def make_team(condition):
    """
    Create contractors for one experimental condition.

    baseline:
        A = arithmetic
        B = writing
        C = coding

    homogeneous:
        A, B, C = general problem solving

    overconfident:
        Same as baseline, but C is instructed to always bid
        with confidence >= 95.
    """

    if condition == "baseline":
        return [
            Contractor(
                name="A",
                skill="arithmetic and numerical calculation",
            ),
            Contractor(
                name="B",
                skill="writing, rewriting, and language tasks",
            ),
            Contractor(
                name="C",
                skill="Python programming and code tasks",
            ),
        ]

    if condition == "homogeneous":
        return [
            Contractor(
                name="A",
                skill="general problem solving",
            ),
            Contractor(
                name="B",
                skill="general problem solving",
            ),
            Contractor(
                name="C",
                skill="general problem solving",
            ),
        ]

    if condition == "overconfident":
        return [
            Contractor(
                name="A",
                skill="arithmetic and numerical calculation",
            ),
            Contractor(
                name="B",
                skill="writing, rewriting, and language tasks",
            ),
            Contractor(
                name="C",
                skill="Python programming and code tasks",
                overconfident=True,
            ),
        ]

    raise ValueError(
        f"Unknown condition: {condition}. "
        "Use baseline, homogeneous, or overconfident."
    )


def run_round(tasks, condition, log=print):
    """
    Run one complete Contract Net round.

    Message counting:
        announcement = 3 messages per task
        bid          = +1 for every bid=True
        award        = +1 when a winner exists
    """

    team = make_team(condition)

    result = RoundResult(tasks=len(tasks))

    log("=" * 70)
    log(f"CONDITION: {condition}")
    log("=" * 70)

    for task in tasks:
        task_id = task["id"]
        description = task["desc"]
        gold = task["gold"]

        log("")
        log("-" * 70)
        log(
            f"[task] id={task_id} "
            f"gold={gold} "
            f"desc={description}"
        )

        # The manager broadcasts the announcement to all 3 contractors.
        result.messages += len(team)

        valid_bids = []

        # Calling contractors in A -> B -> C order is important.
        # If confidence ties, the first contractor keeps priority.
        for contractor in team:
            log(f"[announce] -> contractor {contractor.name}")

            parsed, raw = bid(
                contractor=contractor,
                cid=task_id,
                desc=description,
            )

            # Save raw model output in the experiment log.
            clean_raw = raw.replace("\n", "\\n")
            log(
                f"[raw] {contractor.name}: "
                f"{clean_raw}"
            )

            if parsed is None:
                result.parse_fails += 1

                log(
                    f"[parse-fail] contractor "
                    f"{contractor.name}"
                )

                continue

            log(
                f"[bid] {contractor.name}: "
                f"bid={parsed['bid']} "
                f"confidence={parsed['confidence']} "
                f"reason={parsed['reason']}"
            )

            if parsed["bid"] is True:
                # A true bid is one additional message.
                result.messages += 1

                valid_bids.append(
                    (
                        parsed["confidence"],
                        contractor,
                        parsed,
                    )
                )

        # No contractor submitted a valid bid.
        if not valid_bids:
            result.unassigned += 1

            log("[award] NONE")
            log("[result] unassigned")

            continue

        # Python's stable sort preserves A -> B -> C ordering.
        # Therefore ties go to the contractor that responded first.
        valid_bids.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        winning_confidence, winner, winning_bid = valid_bids[0]

        # Award = one message.
        result.messages += 1

        log(
            f"[award] contractor {winner.name} "
            f"confidence={winning_confidence} "
            f"(gold={gold})"
        )

        if winner.name == gold:
            result.correct += 1
            log("[result] correct")

        else:
            result.misawards += 1
            log(
                f"[result] misaward: "
                f"winner={winner.name}, gold={gold}"
            )

    log("")
    log("=" * 70)
    log("ROUND SUMMARY")
    log(f"tasks       = {result.tasks}")
    log(f"correct     = {result.correct}")
    log(f"messages    = {result.messages}")
    log(f"unassigned  = {result.unassigned}")
    log(f"misawards   = {result.misawards}")
    log(f"parse_fails = {result.parse_fails}")
    log("=" * 70)

    return result