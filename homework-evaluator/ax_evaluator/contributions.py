"""Resolve declared contribution equivalence without inventing semantic matches."""

import copy


def resolve_duplicates(bonuses, observations, catalog):
    """One eligible representative per declared connected group, including cycles.

    The model still discovers contributions and equivalence. We never equate
    artifacts merely because they share a directory, paragraph or source file.
    """
    result = copy.deepcopy(bonuses)
    by_id = {b["id"]: b for b in result}
    definitions = {c["id"]: c for c in catalog}
    groups = {key: {key} for key in by_id}
    recorded = {}
    for value in observations:
        key = value["id"]
        if key not in by_id:
            continue
        recorded.setdefault(key, []).append(value)
        target = value["duplicate_of"]
        if target and target in by_id:
            merged = groups[key] | groups[target]
            for member in merged:
                groups[member] = merged
    seen, decisions = set(), []
    for group in sorted(groups.values(), key=lambda g: sorted(g)):
        identity = tuple(sorted(group))
        if identity in seen or len(group) == 1:
            continue
        seen.add(identity)
        eligible = []
        for key in group:
            records = recorded.get(key, [])
            if records and all(
                all(
                    v["checks"][k] == "supported"
                    for k in (
                        "beyond_required",
                        "implemented",
                        "validated",
                        "meaningful",
                    )
                )
                and (
                    v["checks"]["distinct"] == "supported"
                    or v["checks"]["distinct"] == "contradicted"
                    and v["duplicate_of"] in group
                )
                for v in records
            ):
                eligible.append(key)
        if not eligible:
            continue
        owner = min(
            eligible, key=lambda k: (not bool(definitions[k]["criterion_id"]), k)
        )
        for key in group:
            finding = by_id[key]
            finding["contribution_key"] = owner
            if key == owner:
                finding["status"] = "pass"
                if finding["severity"] == "none":
                    finding["feedback_type"] = "strength"
                    finding["feedback"] = finding["validation"]
            else:
                finding["status"] = "fail"
                finding["limitations"] = (
                    f"동일 기여 {owner}에서 한 번만 가산합니다. "
                    + finding["limitations"]
                )
                if finding["severity"] == "none":
                    finding["feedback_type"] = "suggestion"
                    finding["feedback"] = finding["limitations"]
        decisions.append({"members": list(identity), "representative": owner})
    return result, decisions
