"""Week 03 contract-net experiment scaffold.

The manager, bid parsing, award policy, logging, and experiment runner are
implemented in later commits so the design process remains visible.
"""

from dataclasses import dataclass


CONDITIONS = ("baseline", "homogeneous", "overconfident")


@dataclass(frozen=True)
class Contractor:
    name: str
    skill: str


BASELINE_CONTRACTORS = (
    Contractor("coder", "Python debugging and implementation"),
    Contractor("analyst", "quantitative analysis and metric calculation"),
    Contractor("writer", "clear Korean business writing and editing"),
)


def contractors_for(condition: str) -> tuple[Contractor, ...]:
    """Return the contractor roster for an experimental condition."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    if condition == "homogeneous":
        return tuple(
            Contractor(contractor.name, "general problem solving")
            for contractor in BASELINE_CONTRACTORS
        )
    return BASELINE_CONTRACTORS


def main() -> None:
    raise SystemExit("experiment runner is not implemented yet")


if __name__ == "__main__":
    main()

