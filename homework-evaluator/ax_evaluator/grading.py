"""Apply explicit instructor weights to saved judgments without model calls."""

import math


def weighted_score(report, policy):
    if set(policy) != {"name", "maximum", "groups"} or not policy["name"].strip():
        raise ValueError("Policy needs name, maximum and groups")
    maximum = policy["maximum"]
    if type(maximum) not in {int, float} or not math.isfinite(maximum) or maximum <= 0:
        raise ValueError("maximum must be positive")
    required = set(report["required_ids"])
    seen, groups = set(), []
    weight_total = 0
    for group in policy["groups"]:
        if set(group) != {"name", "weight", "items"}:
            raise ValueError("Each group needs name, weight and items")
        ids, weight = group["items"], group["weight"]
        if (
            not ids
            or len(set(ids)) != len(ids)
            or set(ids) & seen
            or not set(ids) <= required
        ):
            raise ValueError("Required items must be assigned exactly once")
        if type(weight) not in {int, float} or not math.isfinite(weight) or weight <= 0:
            raise ValueError("Each weight must be positive")
        seen.update(ids)
        weight_total += weight
        passed = sum(report["items"][key]["status"] == "pass" for key in ids)
        pending = sum(report["items"][key]["status"] == "pending" for key in ids)
        groups.append(
            {
                "name": group["name"],
                "weight": weight,
                "passed": passed,
                "pending": pending,
                "items": len(ids),
                "earned": maximum * weight * passed / len(ids),
                "possible": maximum * weight * (passed + pending) / len(ids),
            }
        )
    if seen != required or abs(weight_total - 1) > 1e-9:
        raise ValueError("Cover all required items and make weights sum to 1")
    return {
        "policy": policy,
        "earned": sum(g["earned"] for g in groups),
        "possible": sum(g["possible"] for g in groups),
        "maximum": maximum,
        "groups": groups,
        "bonus_separate": report["bonus_earned"],
        "status": "draft-for-human-review",
    }
