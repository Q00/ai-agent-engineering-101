import json

from contractor import Contractor, bid
from trajectory import (
    record_event,
    get_trajectory,
    get_historical_reward,
)


CONFIDENCE_WEIGHT = 0.7
HISTORY_WEIGHT = 0.3


class RoundResult:
    def __init__(self, tasks):
        self.tasks = tasks
        self.correct = 0
        self.messages = 0
        self.unassigned = 0
        self.misawards = 0
        self.parse_fails = 0


def calculate_selection_score(
    confidence,
    historical_reward,
):
    confidence_score = confidence / 100.0

    if historical_reward is None:
        return confidence_score

    return (
        CONFIDENCE_WEIGHT * confidence_score
        + HISTORY_WEIGHT * historical_reward
    )


def run_round(
    tasks,
    team,
    history_snapshot=None,
    log=print,
):
    result = RoundResult(
        tasks=len(tasks)
    )

    if history_snapshot is None:
        history_snapshot = []

    for task in tasks:
        task_id = task["id"]
        category = task["category"]
        desc = task["desc"]
        gold = task["gold"]

        result.messages += len(team)

        bids = []
        all_bids = []

        log(f"[announcement] task={task_id}")
        log(f"  desc={desc}")

        for contractor in team:
            raw, parsed = bid(
                contractor,
                task_id,
                desc,
            )

            if parsed is None:
                result.parse_fails += 1
                log(
                    f"  [parse-fail] "
                    f"{contractor.name}: {raw}"
                )
                continue

            log(
                f"  [bid] {contractor.name}: "
                f"bid={parsed['bid']} "
                f"confidence={parsed['confidence']} "
                f"reason={parsed['reason']}"
            )

            all_bids.append(
                (contractor, parsed)
            )

            if parsed["bid"]:
                trajectory = get_trajectory(
                    contractor.name,
                    category=category,
                    history=history_snapshot,
                )

                historical_reward = get_historical_reward(
                    contractor.name,
                    category=category,
                    history=history_snapshot,
                )

                selection_score = calculate_selection_score(
                    parsed["confidence"],
                    historical_reward,
                )

                log(
                    f"  [trajectory] {contractor.name}: "
                    f"category={category} "
                    f"history_count={len(trajectory)} "
                    f"historical_reward={historical_reward}"
                )

                log(
                    f"  [score] {contractor.name}: "
                    f"confidence={parsed['confidence'] / 100:.3f} "
                    f"selection_score={selection_score:.3f}"
                )

                bids.append(
                    (
                        contractor,
                        parsed,
                        selection_score,
                    )
                )

        # 아무도 입찰하지 않은 경우
        if not bids:
            result.unassigned += 1

            log(
                "  [award] none -> unassigned"
            )

            for contractor, parsed in all_bids:
                record_event(
                    contractor=contractor.name,
                    task_id=task_id,
                    category=category,
                    bid=parsed["bid"],
                    confidence=parsed["confidence"],
                    awarded=False,
                    verdict=None,
                    reward=0.0,
                )

            continue

        # selection_score가 가장 높은 Contractor 선택
        # 동점이면 먼저 입찰한 Contractor
        bids.sort(
            key=lambda x: -x[2]
        )

        winner = bids[0][0]

        result.messages += 1

        if winner.name == gold:
            result.correct += 1
            winner_verdict = "correct"
            winner_reward = 1.0
        else:
            result.misawards += 1
            winner_verdict = "incorrect"
            winner_reward = 0.0

        log(
            f"  [award] "
            f"{winner.name} "
            f"(gold={gold})"
        )

        # 정상적으로 응답한 모든 Contractor의 trajectory 기록
        for contractor, parsed in all_bids:
            awarded = (
                contractor.name == winner.name
            )

            if awarded:
                verdict = winner_verdict
                reward = winner_reward
            else:
                verdict = None
                reward = 0.0

            record_event(
                contractor=contractor.name,
                task_id=task_id,
                category=category,
                bid=parsed["bid"],
                confidence=parsed["confidence"],
                awarded=awarded,
                verdict=verdict,
                reward=reward,
            )

    return result


def load_tasks(path):
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)