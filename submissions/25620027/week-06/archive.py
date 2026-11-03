"""Append-only episode CSV and immutable copies of served Agent Cards."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from models import HEADER, Condition, ExperimentError, Shop

if TYPE_CHECKING:
    from pathlib import Path

    from models import Card, Result


class Archive:
    """An append-only evidence store; raw console logs are owned by the runner."""

    def __init__(self, root: Path) -> None:
        """Locate this run directory without touching existing evidence."""
        self.root: Path = root
        self.results: Path = root / "results.csv"

    def completed(self) -> set[tuple[int, Condition, str]]:
        """Skip recorded cells on resume, including honestly recorded crashes."""
        if not self.results.exists():
            return set()
        with self.results.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != HEADER:
                raise ExperimentError("results.csv", "header differs from contract")
            return {
                (int(row["run"]), Condition(row["condition"]), row["scenario"])
                for row in reader
            }

    def append(self, result: Result) -> None:
        """Write and flush one complete official record without changing old rows."""
        new_file = not self.results.exists()
        with self.results.open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=HEADER)
            if new_file:
                writer.writeheader()
            writer.writerow(result.csv_record())
            stream.flush()

    def card(self, shop: Shop, condition: Condition, card: Card) -> None:
        """Save exactly the endpoint body and detect unintended card changes."""
        filename = {
            Shop.CHEAP: "cheap.json",
            Shop.PRICEY: f"pricey-{condition.value}.json",
        }[shop]
        directory = self.root / "cards"
        directory.mkdir(exist_ok=True)
        target = directory / filename
        contents = card.raw + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != contents:
                raise ExperimentError(filename, "served card changed within condition")
            return
        _ = target.write_text(contents, encoding="utf-8")
