"""Missing and stale buyer evidence remains distinct from measured zero."""

from __future__ import annotations

from typing import TYPE_CHECKING

from r3.buyer_report_models import BuyerReport
from r3.manager_buyer_report import cached_buyer_report

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_report_is_unavailable_without_creating_a_file(tmp_path: Path) -> None:
    # Given: no observation has been produced.
    path = tmp_path / "report.json"
    # When: a slow-loop model reads the cached view.
    view = cached_buyer_report(path, now=1000)
    # Then: missing evidence is not presented as zero performance.
    assert view["status"] == "unavailable"
    assert not path.exists()


def test_report_freshness_depends_on_ledger_and_generation_age(tmp_path: Path) -> None:
    # Given: an old report whose source was fresh at generation time.
    report = BuyerReport(
        generated_at=1000,
        cohort_start=0,
        cohort_end=0,
        ledger_latest_at=1000,
        ledger_fresh=True,
        authoritative_order_times=0,
        local_observation_times=0,
        mature_bands={},
        mature_cohort_start=0,
        mature_cohort_end=0,
        summary="observed cohort",
        notes=(),
    )
    path = tmp_path / "report.json"
    _ = path.write_text(report.model_dump_json())
    # When: the cache is read after its useful age.
    view = cached_buyer_report(path, now=5000)
    # Then: historical measurements are marked stale.
    assert view["status"] == "stale"
    assert view["age_seconds"] == 4000
