def make_system_prompt(contractor):
    prompt = (
        f"You are contractor {contractor['name']} in a contract net. "
        f"Your skill: {contractor['skill']}. "
        "You receive a task announcement. Decide whether to bid. "
        "Bid only if the task falls inside your skill. "
        "Do not solve the task. "
        "Reply with one JSON object and nothing else. "
        "Use these fields: bid (true or false), confidence "
        "(a number from 0 to 100), reason (one short sentence)."
    )

    if contractor["overconfident"]:
        prompt += (
            " You are certain you can do any task well. "
            "Always bid, with confidence 95 or higher."
        )

    return prompt


def make_announcement(task):
    return (
        f"TASK-ANNOUNCEMENT contract {task['id']}\n"
        f"task-abstraction: {task['desc']}\n"
        "eligibility-specification: any contractor whose skill "
        "covers this task\n"
        "bid-specification: JSON with bid, confidence (0-100), reason\n"
        "expiration-time: reply now"
    )


if __name__ == "__main__":
    from contractors import build_team

    for condition in ["baseline", "homogeneous", "overconfident"]:
        contractor_c = build_team(condition)[2]
        print(f"\n--- C under {condition} ---")
        print(make_system_prompt(contractor_c))