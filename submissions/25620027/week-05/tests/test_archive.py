import csv
from pathlib import Path

from market.archive import HEADER, EpisodeKey, ResultArchive, ResultRow
from market.models import (
    Action,
    Condition,
    EpisodeSummary,
    EventRecord,
    EventType,
    NegotiationId,
    Role,
    Status,
)


def test_archive_is_resume_safe_and_preserves_evidence(tmp_path: Path) -> None:
    archive = ResultArchive(tmp_path)
    summary = EpisodeSummary(
        negotiation_id=NegotiationId("neg-test"),
        condition=Condition.SERVER_INJECT,
        scenario="1",
        deal_possible=1,
        status=Status.DEAL,
        price=145,
        correct=1,
        violation=0,
        attempted_violations=1,
        refused_calls=1,
        turns=3,
        tool_calls=4,
        turn=Role.BUYER,
        recovered_refusals=1,
    )
    event = EventRecord(
        sequence=1,
        negotiation_id=NegotiationId("neg-test"),
        event=EventType.CALL_REFUSED,
        role=Role.BUYER,
        action=Action.ACCEPT,
        price=170,
        reason="outside_authorized_limit",
        turn_index=2,
    )
    row = ResultRow.from_summary("server_inject-r1-s1", summary, "recovered")

    archive.append(row, (event,), ("refused", "recovered"))

    key = EpisodeKey(
        run="server_inject-r1-s1",
        condition=Condition.SERVER_INJECT,
        scenario="1",
    )
    assert ResultArchive(tmp_path).contains(key)
    with (tmp_path / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == HEADER
    assert rows[1][7:10] == ["0", "1", "1"]
    assert (tmp_path / "logs" / "server_inject-r1-s1.txt").read_text(
        encoding="utf-8"
    ) == "refused\nrecovered\n"
    assert '"event":"CALL_REFUSED"' in (
        tmp_path / "extension" / "shadow_events.jsonl"
    ).read_text(encoding="utf-8")


def test_archive_can_keep_follow_up_evidence_in_its_own_root(tmp_path: Path) -> None:
    ResultArchive(tmp_path, extension_root=tmp_path)

    assert (tmp_path / "results.csv").exists()
    assert (tmp_path / "shadow_events.jsonl").exists()
    assert (tmp_path / "injection_trace.csv").exists()
    assert not (tmp_path / "extension").exists()
