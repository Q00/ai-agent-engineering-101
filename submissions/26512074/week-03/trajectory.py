import json
import os


HISTORY_FILE = "history.json"


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(
            history,
            f,
            ensure_ascii=False,
            indent=2,
        )


def record_event(
    contractor,
    task_id,
    category,
    bid,
    confidence,
    awarded,
    verdict,
    reward,
):
    history = load_history()

    history.append(
        {
            "contractor": contractor,
            "task_id": task_id,
            "category": category,
            "bid": bid,
            "confidence": confidence,
            "awarded": awarded,
            "verdict": verdict,
            "reward": reward,
        }
    )

    save_history(history)


def get_trajectory(
    contractor,
    category=None,
    history=None,
):
    if history is None:
        history = load_history()

    trajectory = [
        item
        for item in history
        if item["contractor"] == contractor
    ]

    if category is not None:
        trajectory = [
            item
            for item in trajectory
            if item["category"] == category
        ]

    return trajectory


def get_historical_reward(
    contractor,
    category=None,
    history=None,
):
    trajectory = get_trajectory(
        contractor,
        category=category,
        history=history,
    )

    awarded_tasks = [
        item
        for item in trajectory
        if item["awarded"] is True
    ]

    if not awarded_tasks:
        return None

    rewards = [
        item["reward"]
        for item in awarded_tasks
    ]

    return sum(rewards) / len(rewards)