"""Retain useful context while bounding decision storage across restarts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from market_a2a.memory import Memory
from market_a2a.memory_retention import (
    MAX_FILE_BYTES,
    MAX_PER_SHOP,
    MAX_RECORDS,
    MAX_SHOPS,
    retained,
)
from tests.test_memory_selection import observation

if TYPE_CHECKING:
    from pathlib import Path


def test_old_and_oversized_context_is_pruned_without_editing_audit_files(
    tmp_path: Path,
) -> None:
    # Given: useful recent experience, expired facts and a very large old observation.
    now = datetime.now(UTC)
    old = observation("expired").model_copy(
        update={"observed_at": now - timedelta(days=15)}
    )
    current = observation("recent").model_copy(update={"observed_at": now})
    oversized = current.model_copy(update={"selection_reason": "x" * 9000})
    receipt_log = tmp_path / "settlement-audit.jsonl"
    _ = receipt_log.write_text('{"signed_evidence":"preserve-exactly"}\n')
    path = tmp_path / "buyer-memory.json"
    # When: replace the decision snapshot, then reload it as another process would.
    Memory(shops={"Stable": (old, oversized, current)}).save(path)
    reloaded = Memory.load(path)
    # Then: only useful bounded context is recalled; audit evidence is untouched.
    assert reloaded.shops["Stable"] == (current,)
    assert path.stat().st_size <= MAX_FILE_BYTES
    assert path.with_suffix(".context.md").is_file()
    assert receipt_log.read_text() == '{"signed_evidence":"preserve-exactly"}\n'


def test_storage_stays_bounded_with_many_shops_and_long_history(tmp_path: Path) -> None:
    # Given: more shops and records than any retention dimension permits.
    now = datetime.now(UTC)
    trade = observation("own-task").model_copy(update={"observed_at": now})
    source = {f"shop-{index}": (trade,) * 400 for index in range(100)}
    # When: save and reopen the actual store, rather than just checking a helper.
    path = tmp_path / "buyer-memory.json"
    Memory(shops=source).save(path)
    result = Memory.load(path)
    # Then: both records and bytes are bounded independently of original history.
    assert len(result.shops) <= MAX_SHOPS
    assert all(len(trades) <= MAX_PER_SHOP for trades in result.shops.values())
    assert sum(map(len, result.shops.values())) <= MAX_RECORDS
    assert path.stat().st_size <= MAX_FILE_BYTES
    assert path.with_suffix(".context.md").stat().st_size < 100_000
    print(
        {
            "input_records": 40000,
            "retained": sum(map(len, result.shops.values())),
            "file_bytes": path.stat().st_size,
        }
    )


def test_legacy_unknown_date_is_not_invented_and_newest_fact_takes_priority() -> None:
    # Given: a legacy observation has no reliable timestamp.
    now = datetime.now(UTC)
    legacy = observation("legacy")
    latest = observation("new").model_copy(update={"observed_at": now})
    # When: retention competes for a per-shop window with dated new records.
    result = retained({"Stable": (legacy, *((latest,) * 250))}, now)
    # Then: current evidence takes priority without inventing a date for legacy data.
    assert legacy.observed_at is None
    assert len(result["Stable"]) == MAX_PER_SHOP
    assert all(
        trade.purchase.receipt == latest.purchase.receipt for trade in result["Stable"]
    )


def test_byte_limit_applies_before_record_count_limit(tmp_path: Path) -> None:
    # Given: each record is admissible but a thousand together exceed2 MiB.
    current = observation("large-context").model_copy(
        update={
            "observed_at": datetime.now(UTC),
            "selection_reason": "x" * 6000,
        }
    )
    source = {f"shop-{index}": (current,) * 100 for index in range(10)}
    path = tmp_path / "buyer-memory.json"
    # When: persist then reload the same file that buyer selection will read.
    Memory(shops=source).save(path)
    result = Memory.load(path)
    # Then: the byte cap removes context even though total count is below2048.
    assert path.stat().st_size <= MAX_FILE_BYTES
    assert sum(map(len, result.shops.values())) < 1000
