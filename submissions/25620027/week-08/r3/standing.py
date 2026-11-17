"""Where we stand on the graded board: rank, points and headroom per component.

The midterm grade is the Players-table rank of shop ALL + buyer ALL. Each ALL is
a weighted sum of percentiles among the participants with a deal (number below
us / (n - 1)), so the board numbers can be rebuilt exactly (checked on 11/4 to
the third decimal). The strategy, tactical and advisor models get this table so
every change can name the component it serves: keep the strengths, work on the
components with the most points left.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

SHOP_WEIGHTS: Final = (("profit", 0.5), ("revenue", 0.3), ("stars", 0.2))
BUYER_WEIGHTS: Final = (
    ("surplus", 0.5),
    ("forum", 0.3),
    ("deals", 0.1),
    ("posts", 0.1),
)
# A component at or above this percentile is a strength to defend.
STRONG: Final = 0.85


def _num(value: object) -> float:
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def _rows(board: Mapping[str, object], key: str) -> list[dict[str, object]]:
    rows = board.get(key)
    return [
        r
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and _num(r.get("deals")) > 0
    ]


def _component(
    rows: list[dict[str, object]], mine: dict[str, object], metric: str, weight: float
) -> dict[str, object]:
    ours = _num(mine.get(metric))
    values = sorted((_num(r.get(metric)) for r in rows), reverse=True)
    n = len(values)
    below = sum(v < ours for v in values)
    above = [v for v in values if v > ours]
    percentile = below / (n - 1) if n > 1 else 1.0
    points = weight * percentile
    return {
        "metric": metric,
        "weight": weight,
        "rank": len(above) + 1,
        "of": n,
        "value": round(ours, 3),
        "percentile": round(percentile, 3),
        "points": round(points, 4),
        "points_left": round(weight - points, 4),
        # what passing one more participant is worth, and how far that is
        "next_up_value": round(above[-1], 3) if above else None,
        "gap_up": round(above[-1] - ours, 3) if above else None,
        "points_per_rank": round(weight / (n - 1), 4) if n > 1 else 0.0,
        "role": "strength" if percentile >= STRONG else "weakness",
    }


def standing(board: object, name: str) -> dict[str, object]:
    """Rebuild our graded position from the public board; empty when unknown."""
    if not isinstance(board, dict):
        return {}
    shops, buyers = _rows(board, "shops"), _rows(board, "buyers")
    shop = next((r for r in shops if str(r.get("name")) == name), None)
    buyer = next((r for r in buyers if str(r.get("name")) == name), None)
    components: list[dict[str, object]] = []
    if shop is not None:
        components += [
            {"side": "shop", **_component(shops, shop, m, w)} for m, w in SHOP_WEIGHTS
        ]
    if buyer is not None:
        components += [
            {"side": "buyer", **_component(buyers, buyer, m, w)}
            for m, w in BUYER_WEIGHTS
        ]
    players = [
        p
        for p in (board.get("players") or [])
        if isinstance(p, dict) and not p.get("house")
    ]
    me = next(
        (
            p
            for p in players
            if isinstance(p.get("shop"), dict) and p["shop"].get("name") == name
        ),
        None,
    )
    out: dict[str, object] = {
        "objective": "Players-table rank of shop ALL + buyer ALL at 11/17 23:59 KST",
        "components": sorted(components, key=lambda c: -_num(c["points_left"])),
    }
    if me is not None:
        totals = sorted((_num(p.get("total")) for p in players), reverse=True)
        total = _num(me.get("total"))
        above = [t for t in totals if t > total]
        below = [t for t in totals if t < total]
        out.update(
            total=round(total, 3),
            rank=len(above) + 1,
            of=len(totals),
            gap_to_next_up=round(above[-1] - total, 3) if above else None,
            lead_over_next_down=round(total - below[0], 3) if below else None,
        )
    return out
