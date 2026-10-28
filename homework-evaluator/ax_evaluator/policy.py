"""Collect optional bonus rules once, before grading a cohort."""

import copy


def validate_policy(policy):
    if set(policy) != {"explicit_criteria", "open_ended", "open_points", "cap"}:
        raise ValueError("Unexpected bonus policy fields")
    if type(policy["open_ended"]) is not bool:
        raise ValueError("open_ended must be boolean")
    if type(policy["open_points"]) is not int or policy["open_points"] != 1:
        raise ValueError("Every bonus contribution must be worth exactly 1 point")
    cap = policy["cap"]
    if cap is not None and (type(cap) is not int or cap < 0):
        raise ValueError("cap must be null or a nonnegative integer")
    if not isinstance(policy["explicit_criteria"], list):
        raise ValueError("explicit_criteria must be an array")
    seen = set()
    for criterion in policy["explicit_criteria"]:
        if set(criterion) != {"id", "title", "rule", "points"}:
            raise ValueError("A bonus criterion needs id, title, rule and points")
        if any(
            not isinstance(criterion[k], str) or not criterion[k].strip()
            for k in ("id", "title", "rule")
        ):
            raise ValueError("Bonus criterion text cannot be empty")
        if criterion["id"] in seen:
            raise ValueError("Duplicate bonus criterion ID")
        seen.add(criterion["id"])
        if type(criterion["points"]) is not int or criterion["points"] != 1:
            raise ValueError("Every bonus criterion must be worth exactly 1 point")


def interview(ask=input):
    """Interactive input is grader policy, never inferred from student documents."""
    answer = ask("명시적인 가산점 기준이 있나요? [y/N]: ").strip().lower()
    if answer not in {"", "y", "yes", "n", "no"}:
        raise ValueError("Answer y or n")
    criteria = []
    if answer in {"y", "yes"}:
        while True:
            title = ask("가산 항목 이름 (입력 완료 시 Enter): ").strip()
            if not title:
                break
            rule = ask("충족 조건과 필요한 검증 근거: ").strip()
            criteria.append(
                dict(id=f"E{len(criteria) + 1}", title=title, rule=rule, points=1)
            )
        if not criteria:
            raise ValueError("Explicit criteria selected but none entered")
    # Meaningful extras remain eligible even when explicit criteria are supplied.
    cap_text = ask("전체 가산점 상한 (Enter: 상한 없음): ").strip()
    policy = dict(
        explicit_criteria=criteria,
        open_ended=True,
        open_points=1,
        cap=int(cap_text) if cap_text else None,
    )
    validate_policy(policy)
    return policy


def apply_policy(rubric, policy):
    validate_policy(policy)
    result = copy.deepcopy(rubric)
    result["bonus_policy"] = copy.deepcopy(policy)
    return result


def bonus_points(bonus, policy):
    if bonus["status"] != "pass":
        return 0
    if not bonus["criterion_id"]:
        return policy["open_points"]
    return next(
        c["points"]
        for c in policy["explicit_criteria"]
        if c["id"] == bonus["criterion_id"]
    )
