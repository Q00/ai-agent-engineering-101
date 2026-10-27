from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[3]


class _Shop(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    name: str
    deals: int
    visits: int
    share: float
    revenue: int
    stars: float | None


class _Board(BaseModel):
    round: int
    shops: tuple[_Shop, ...]


class _Http(BaseModel):
    method: str
    url: str


class _Selection(BaseModel):
    index: int
    reason: str


class _VisitSummary(BaseModel):
    source: str
    source_sha256: str
    source_lines: int
    round2_orders: int
    orders_with_selected_card: int
    bright_living_selected_orders: int
    selected_card_counts: dict[str, int]
    reasons_mentioning_bright_living_ineligibility: int
    example_selection_lines: tuple[str, ...]


def _visits(relative: str) -> _VisitSummary:
    path = ROOT / relative
    raw = path.read_bytes()
    lines = raw.decode("utf-8").splitlines()
    current = ""
    orders: set[str] = set()
    cards: dict[str, str] = {}
    reasons: dict[str, str] = {}
    examples: list[str] = []
    for number, line in enumerate(lines, 1):
        order = re.search(r"\[order (o-[A-Za-z0-9-]+)\].*, round ([0-9]+)$", line)
        if order:
            current = order[1] if order[2] == "2" else ""
            if current:
                orders.add(current)
        if not current:
            continue
        if "[A2A HTTP] " in line:
            message = _Http.model_validate_json(line.split("[A2A HTTP] ", 1)[1])
            if message.method == "GET" and message.url.endswith("/agent-card.json"):
                cards[current] = message.url
        if "[MARKET SELECTION] " in line:
            choice = _Selection.model_validate_json(
                line.split("[MARKET SELECTION] ", 1)[1]
            )
            reasons[current] = choice.reason
            if "bright-living" in choice.reason and re.search(
                r"ineligible|operator.direct|blocked|exclud", choice.reason, re.I
            ) and len(examples) < 2:
                examples.append(f"{relative}:{number}: {line}")
    counts = Counter(cards.values())
    return _VisitSummary(
        source=relative,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_lines=len(lines), round2_orders=len(orders),
        orders_with_selected_card=len(cards),
        bright_living_selected_orders=sum(
            "market.ourolabs.ai/shop/bright-living/" in url for url in cards.values()
        ),
        selected_card_counts=dict(counts),
        reasons_mentioning_bright_living_ineligibility=sum(
            "bright-living" in reason and bool(re.search(
                r"ineligible|operator.direct|blocked|exclud", reason, re.I
            )) for reason in reasons.values()
        ),
        example_selection_lines=tuple(examples),
    )


if __name__ == "__main__":
    target = ROOT / "logs"
    before_source = ".omo/evidence/market-monitor-20261021/snapshot-1608/board.json"
    after_source = ".omo/evidence/top5-upgrade-20261021/board.raw"
    snapshots = []
    for source in (before_source, after_source):
        board = _Board.model_validate_json((ROOT / source).read_bytes())
        assert board.round == 2
        shop = next(shop for shop in board.shops if shop.name == "25620027")
        snapshots.append(shop)
    before, after = snapshots
    price_lines = [
        f"before_source={before_source}", f"after_source={after_source}",
        f"before={before.model_dump_json()}", f"after={after.model_dump_json()}",
        f"before_cumulative_mean={before.revenue / before.deals:.6f}",
        f"after_cumulative_mean={after.revenue / after.deals:.6f}",
        f"delta_deals={after.deals-before.deals}",
        f"delta_revenue={after.revenue-before.revenue}",
        "interval_mean=" + str(
            (after.revenue-before.revenue)/(after.deals-before.deals)
        ),
    ]
    price_dir = target / "challenge-1"
    price_dir.mkdir(parents=True, exist_ok=True)
    _ = (price_dir / "summary.log").write_text("\n".join(price_lines)+"\n")
    safety_dir = target / "challenge-2"
    safety_dir.mkdir(parents=True, exist_ok=True)
    for label, source in (
        ("before", "logs/market-kit.log"),
        ("after", ".omo/evidence/final-submission-20261027/vps-market-kit-20261027.log"),
    ):
        summary = _visits(source)
        _ = (safety_dir / f"{label}.json").write_text(summary.model_dump_json(indent=2))
        print(summary.model_dump_json())
    old_lines = (ROOT / "logs/market-kit.log").read_text().splitlines()
    _ = (safety_dir / "observed-response.log").write_text(
        f"logs/market-kit.log:8483: {old_lines[8482]}\n"
    )
    print("\n".join(price_lines))
