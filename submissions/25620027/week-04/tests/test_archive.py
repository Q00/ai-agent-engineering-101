from pathlib import Path

from acl_lab.archive import ResultArchive
from acl_lab.domain import Condition, EpisodeResult, Outcome, ResultRecord


def test_archive_skips_only_an_exact_completed_episode(tmp_path: Path) -> None:
    # Given
    archive = ResultArchive(tmp_path)
    result = EpisodeResult(
        outcome=Outcome.NO_DEAL,
        price=None,
        correct=1,
        violation=0,
        turns=1,
        format_errors=0,
        reader_calls=1,
        note="tokens=2",
    )
    archive.append(
        ResultRecord(
            run=1,
            condition=Condition.FREE,
            scenario="3",
            deal_possible=0,
            result=result,
        )
    )

    # When
    completed = archive.completed_keys()

    # Then
    assert (1, Condition.FREE, "3") in completed
    assert (2, Condition.FREE, "3") not in completed


def test_archive_keeps_exact_assignment_header(tmp_path: Path) -> None:
    # Given
    archive = ResultArchive(tmp_path)

    # When
    header = archive.results_path.read_text(encoding="utf-8").splitlines()[0]

    # Then
    assert header == (
        "run,condition,scenario,deal_possible,outcome,price,correct,violation,turns,"
        "format_errors,reader_calls,note"
    )
