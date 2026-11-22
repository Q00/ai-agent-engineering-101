"""Graded rank, contribution and remaining points from the official public board.

Shop ALL weights book value, revenue and adjusted review rank. Participation
includes stock-only purchasers, and tied values share the middle percentile.
Buyer ALL weights the saved rate and the raw forum rating under Patch 3.5.
Forum ranks remain diagnostic; they do not award percentile points. Components
are exposed only when they match their official ALL to three decimals. Public
buyer forum nulls share the zero-value rank, checked against captured boards.
The primary view is the official student-only Class; All is diagnostic only.
Official student totals remain authoritative, even when reconstruction fails.
"""

from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING, Final, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

SHOP_WEIGHTS: Final = (("book", 0.5), ("revenue", 0.3), ("stars_rank", 0.2))
BUYER_WEIGHTS: Final = (
    ("saved", 0.5),
    ("forum", 0.3),
    ("deals", 0.1),
    ("posts", 0.1),
)
SCORING_VERSION: Final = "r3_patch35_forum_value"
# A percentile or raw forum rating at this level is a strength to defend.
STRONG: Final = 0.85


def _num(value: object) -> float:
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def finite_number(value: object) -> bool:
    """Return whether a public board value is a finite number."""
    try:
        return isfinite(float(str(value)))
    except ValueError:
        return False


def board_rows(board: Mapping[str, object], key: str) -> list[dict[str, object]]:
    """Read dictionary rows from a named public board collection."""
    raw = board.get(key)
    if not isinstance(raw, list):
        return []
    return [
        cast("dict[str, object]", row)
        for row in cast("list[object]", raw)
        if isinstance(row, dict)
    ]


def _rows(board: Mapping[str, object], key: str) -> list[dict[str, object]]:
    return [
        r
        for r in board_rows(board, key)
        if (
            _num(r.get("deals")) > 0
            or (key == "shops" and _num(r.get("stock_units")) > 0)
        )
    ]


def _component(
    rows: list[dict[str, object]], mine: dict[str, object], metric: str, weight: float
) -> dict[str, object]:
    ours = _num(mine.get(metric))
    values = sorted((_num(r.get(metric)) for r in rows), reverse=True)
    n = len(values)
    below = sum(v < ours for v in values)
    above = [v for v in values if v > ours]
    ties = sum(v == ours for v in values)
    percentile = (below + (ties - 1) / 2) / (n - 1) if n > 1 else 1.0
    points = weight * percentile
    precision = 4 if metric == "saved" else 3
    return {
        "metric": metric,
        "score_basis": "percentile",
        "weight": weight,
        "rank": len(above) + 1,
        "of": n,
        "value": round(ours, precision),
        "percentile": round(percentile, 3),
        "points": round(points, 8),
        "points_left": round(weight - points, 8),
        # what passing one more participant is worth, and how far that is
        "next_up_value": round(above[-1], precision) if above else None,
        "gap_up": round(above[-1] - ours, precision) if above else None,
        "points_per_rank": round(weight / (n - 1), 4) if n > 1 else 0.0,
        "role": "strength" if percentile >= STRONG else "weakness",
    }


def _buyer_component(
    rows: list[dict[str, object]], mine: dict[str, object], metric: str, weight: float
) -> dict[str, object]:
    part = _component(rows, mine, metric, weight)
    if metric == "forum":
        value = _num(mine.get(metric))
        part.update(
            score_basis="value",
            percentile_is_diagnostic=True,
            points=round(weight * value, 8),
            points_left=round(weight * (1 - value), 8),
            points_per_rank=0.0,
            points_per_0_01_value=round(weight * 0.01, 8),
            role="strength" if value >= STRONG else "weakness",
        )
    return part


def _standing(board: object, name: str) -> dict[str, object]:
    """Rebuild one explicitly projected cohort from official role totals."""
    if not isinstance(board, dict):
        return {}
    board = cast("dict[str, object]", board)
    players = [p for p in board_rows(board, "players") if not p.get("house")]
    me = next(
        (
            p
            for p in players
            if any(
                isinstance(p.get(side), dict)
                and cast("dict[str, object]", p[side]).get("name") == name
                for side in ("shop", "buyer")
            )
        ),
        None,
    )
    shop_role = me.get("shop") if me is not None else None
    buyer_role = me.get("buyer") if me is not None else None
    shop_name = (
        cast("dict[str, object]", shop_role).get("name")
        if isinstance(shop_role, dict)
        else name
    )
    buyer_name = (
        cast("dict[str, object]", buyer_role).get("name")
        if isinstance(buyer_role, dict)
        else name
    )
    shops, buyers = _rows(board, "shops"), _rows(board, "buyers")
    shop = next((r for r in shops if r.get("name") == shop_name), None)
    buyer = next((r for r in buyers if r.get("name") == buyer_name), None)
    components: list[dict[str, object]] = []
    shop_check: dict[str, object] = {"status": "unavailable"}
    if shop is not None and all(
        finite_number(row.get(metric)) for row in shops for metric, _ in SHOP_WEIGHTS
    ):
        shop_parts = [
            {"side": "shop", **_component(shops, shop, m, w)} for m, w in SHOP_WEIGHTS
        ]
        rebuilt = round(sum(_num(c["points"]) for c in shop_parts), 3)
        official = shop.get("all")
        matched = finite_number(official) and rebuilt == round(_num(official), 3)
        shop_check = {
            "status": "matched" if matched else "unverified",
            "rebuilt": rebuilt,
            "official": official,
        }
        if matched:
            components += shop_parts
    buyer_check: dict[str, object] = {"status": "unavailable"}
    # Unknown deal counts make the participant denominator unknown as well.
    buyer_inputs_valid = all(
        finite_number(row.get("deals")) for row in board_rows(board, "buyers")
    ) and all(
        metric in row
        and (finite_number(row[metric]) or (metric == "forum" and row[metric] is None))
        for row in buyers
        for metric, _ in BUYER_WEIGHTS
    )
    # saved = sum(surplus / list_price) / (deals + 100): at most one.
    # Legacy boards used currency here; matching ranks alone cannot detect that.
    saved_is_rate = all(_num(row.get("saved")) <= 1 for row in buyers)
    forum_is_rating = all(0 <= _num(row.get("forum")) <= 1 for row in buyers)
    if (
        buyer is not None
        and buyer_inputs_valid
        and saved_is_rate
        and forum_is_rating
        and finite_number(buyer.get("all"))
    ):
        buyer_parts = [
            {"side": "buyer", **_buyer_component(buyers, buyer, m, w)}
            for m, w in BUYER_WEIGHTS
        ]
        rebuilt = round(sum(_num(c["points"]) for c in buyer_parts), 3)
        official = buyer["all"]
        matched = rebuilt == round(_num(official), 3)
        buyer_check = {
            "status": "matched" if matched else "unverified",
            "rebuilt": rebuilt,
            "official": official,
        }
        if matched:
            components += buyer_parts
    out: dict[str, object] = {
        "objective": "Players-table rank of shop ALL + buyer ALL at 11/17 23:59 KST",
        "components": sorted(components, key=lambda c: -_num(c["points_left"])),
        "shop_reconstruction": shop_check,
        "buyer_reconstruction": buyer_check,
        "scoring_version": SCORING_VERSION,
        "scoring_verified": (
            shop_check["status"] == "matched" and buyer_check["status"] == "matched"
        ),
    }
    if me is not None and finite_number(me.get("total")):
        out["total"] = round(_num(me["total"]), 3)
    if me is not None and all(finite_number(p.get("total")) for p in players):
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
            gap_to_first=round(totals[0] - total, 3),
        )
    return out


def board_owner(row: Mapping[str, object]) -> str:
    """Normalize a public board owner's login for identity comparisons."""
    owner = row.get("owner")
    if not isinstance(owner, dict):
        return ""
    login = cast("dict[str, object]", owner).get("login")
    return login.strip().casefold() if isinstance(login, str) else ""


def class_rows(
    board: Mapping[str, object],
    players: list[dict[str, object]],
    side: str,
    role: str,
) -> list[dict[str, object]]:
    """Use selected identities and their owners; missing references stay unknown."""
    selected: dict[str, str] = {}
    references_known = True
    for player in players:
        if role not in player:
            references_known = False
            continue
        identity = player.get(role)
        if identity is None:
            continue
        if not isinstance(identity, dict):
            references_known = False
            continue
        identity_name = cast("dict[str, object]", identity).get("name")
        if isinstance(identity_name, str) and identity_name:
            if identity_name in selected and selected[identity_name] != board_owner(
                player
            ):
                references_known = False
            selected[identity_name] = board_owner(player)
        else:
            references_known = False
    rows = [
        row.copy()
        for row in board_rows(board, side)
        if isinstance((identity_name := row.get("name")), str)
        and selected.get(identity_name) == board_owner(row)
    ]
    if not references_known or {row["name"] for row in rows} != set(selected):
        # Missing selected rows make the component denominator unknown.
        # The official Class total remains available independently.
        rows = []
    return rows


def standing(
    board: object, name: str, *, scope: Literal["class", "all"] = "class"
) -> dict[str, object]:
    """Grade Class from its official roster/values; never substitute All for it."""
    if not isinstance(board, dict):
        return {}
    board = cast("dict[str, object]", board)
    diagnostic = {**_standing(board, name), "scope": "all"}
    if scope == "all":
        return diagnostic
    players = [
        p
        for p in board_rows(board, "players")
        if p.get("student") is True and not p.get("house")
    ]
    owners = {board_owner(p) for p in players}
    unavailable: dict[str, object] = {
        "scope": "class",
        "objective": "Official Players Class rank among students at 11/17 23:59 KST",
        "components": [],
        "shop_reconstruction": {"status": "unavailable"},
        "buyer_reconstruction": {"status": "unavailable"},
        "class_roster_status": "unavailable",
        "scoring_version": SCORING_VERSION,
        "scoring_verified": False,
        "all_diagnostic": diagnostic,
    }
    if not owners or "" in owners:
        return unavailable
    mine = next(
        (
            p
            for p in players
            if any(
                isinstance(p.get(side), dict)
                and cast("dict[str, object]", p[side]).get("name") == name
                for side in ("shop", "buyer")
            )
        ),
        None,
    )
    if mine is None:
        return unavailable
    projected: dict[str, object] = {
        "players": [{**p, "total": p.get("student_total")} for p in players]
    }
    for side, role, official in (
        ("shops", "shop", "student_shop"),
        ("buyers", "buyer", "student_buyer"),
    ):
        rows = class_rows(board, players, side, role)
        target = mine.get(role)
        target_name = (
            cast("dict[str, object]", target).get("name")
            if isinstance(target, dict)
            else None
        )
        for row in rows:
            if row.get("name") == target_name:
                row["all"] = mine.get(official)
        projected[side] = rows
    table = _standing(projected, name)
    table.update(
        scope="class",
        objective=unavailable["objective"],
        class_roster_status="available",
        all_diagnostic=diagnostic,
    )
    return table
