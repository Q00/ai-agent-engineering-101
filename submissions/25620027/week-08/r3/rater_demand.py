"""SKUs our most important raters buy elsewhere, so restock carries them.

11/14 21:0x (strategy brief task 2-T): totato2203-r3 bought 903 units in 12 h, 52
from us; its last 24 h of reviews of us are all 5 stars but its mean is 3.09 over
735, and 600 of the 903 were SKUs we held no A/B of (343 goods under the restock
price floor). q00 (our mean 3.28, recent 4.92) buys by category on a budget. Their
top SKUs over the last DEMAND_HOURS are bought regardless of goods_min_list_price;
grade, food shelf, overpriced/stuck blocks, cash and sales caps still apply.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections import Counter
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

RATER_DEMAND_FILE: Final = "r3-rater-demand.json"
RATER_BUYERS: Final = ("totato2203-r3", "q00")
DEMAND_HOURS: Final = 12.0
TOP_SKUS: Final = 15
MAX_AGE_SECONDS: Final = 2 * 3600.0
# 11/15 19:2x (owner, Codex rating review): bjeon01 rated its last 98 deals here
# 5 (round mean 4.848); these SKUs sold elsewhere while we held none. They follow
# the rater buyers' SKUs (restock sorts them last) and pass the usual food shelf,
# price and new-SKU checks.
EXTRA_SKUS: Final = (
    "G05171239",
    "G3434758a",
    "Gf0e58a76",
    "G1831f2c0",
    "G0d52d508",
    "G329bfeec",
    "G344fc18d",
    "G06f2c594",
    "G16822d88",
    "G0132ff94",
    "G9a240832",
)
# 11/16 16:3x (owner): two buyers rating every other shop about 5 have never rated
# us in round 3: ovotnwjd-maker (2,116 buys in 24 h) and JangSeongWoong (917). Their
# top food SKUs that pass the restock filter and that we hold none of get a small
# trial ahead of the other listed SKUs (restock.plan sorts them first).
NEW_BUYER_SKUS: Final = (
    "G38519afd",  # JangSeongWoong 162 in 24 h (왕갈비탕)
    "Gf0e58a76",  # ovotnwjd-maker 137 (신동진쌀 20kg)
    "G344fc18d",  # ovotnwjd-maker 130 (경기미 10kg)
    "G124d3ea5",  # ovotnwjd-maker 71 (돌산갓김치)
    "G29fc9227",  # ovotnwjd-maker 52 (비비고 포기김치)
    "G1ee4e4f8",  # ovotnwjd-maker 50 (종가집 총각김치)
    "G35351812",  # JangSeongWoong 36 (통참깨)
    "Ge508020a",  # JangSeongWoong 26 (소곱창전골)
    "G8bca90eb",  # ovotnwjd-maker 29 (떡갈비)
    "G2b54c83e",  # JangSeongWoong 20 (감자탕)
)
# Owner 11/14 20:5x: the pillow cover is not stocked (q00 gets the pillow filling).
EXCLUDED_SKUS: Final = frozenset({"Ga2e3075f"})


def find_rater_demand(db: Path, now: float | None = None) -> list[str]:
    """Return the top SKUs the rater buyers settled lately, most first."""
    stamp = now if now is not None else time.time()
    counts: Counter[str] = Counter()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        marks = ",".join("?" for _ in RATER_BUYERS)
        for (sku,) in con.execute(
            f"select sku from rows where kind='settle' and buyer in ({marks})"  # noqa: S608 - fixed placeholders
            " and ts>=?",
            (*RATER_BUYERS, stamp - DEMAND_HOURS * 3600),
        ):
            if sku and sku not in EXCLUDED_SKUS:
                counts[str(sku)] += 1
    finally:
        con.close()
    top = [sku for sku, _ in counts.most_common(TOP_SKUS)]
    listed = top + [sku for sku in EXTRA_SKUS if sku not in top]
    return listed + [sku for sku in NEW_BUYER_SKUS if sku not in listed]


def write_rater_demand(path: Path, skus: list[str], now: float) -> None:
    """Persist the list (restock reads it every run)."""
    with contextlib.suppress(OSError):
        _ = path.write_text(json.dumps({"ts": now, "skus": skus}), "utf-8")


def load_rater_demand(path: Path, now: float | None = None) -> tuple[str, ...]:
    """Return the current list; a missing or stale file means none."""
    stamp = now if now is not None else time.time()
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, dict) or stamp - float(raw.get("ts") or 0) > MAX_AGE_SECONDS:
        return ()
    skus = raw.get("skus")
    return tuple(str(s) for s in skus) if isinstance(skus, list) else ()
