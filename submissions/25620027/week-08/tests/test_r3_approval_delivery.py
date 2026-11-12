"""Important approval requests persist until transport success is acknowledged."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from deploy.vps import notify_r3

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_failed_telegram_delivery_retries_without_a_new_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def command(args: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "message": "#7 코드 수정 승인 요청",
                    "proposal_ids": [7],
                }
            ),
        )

    monkeypatch.setattr(notify_r3.subprocess, "run", command)
    monkeypatch.setattr(notify_r3, "BASE", tmp_path)
    monkeypatch.setattr(notify_r3, "vault_job", lambda *_a, **_kw: None)
    monkeypatch.setattr(notify_r3, "send", lambda *_a, **_kw: False)
    state = tmp_path / "notify.json"
    notify_r3.advisor("21", state, dry_run=False, force=True)
    assert len(calls) == 1
    assert notify_r3.advice_outbox(state).exists()
    monkeypatch.setattr(notify_r3, "send", lambda *_a, **_kw: True)
    assert notify_r3.retry_advice(state, dry_run=False)
    assert len(calls) == 2
    assert "--ack" in calls[-1]
    assert calls[-1][-1] == "7"
    assert not notify_r3.advice_outbox(state).exists()


def test_successful_send_without_ack_keeps_request_and_dry_run_never_consumes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "notify.json"
    notify_r3.queue_advice(state, "#7 승인 요청", [7], dry_run=False)
    monkeypatch.setattr(notify_r3, "send", lambda *_a, **_kw: True)
    monkeypatch.setattr(notify_r3, "ack_advice", lambda *_a, **_kw: False)
    assert not notify_r3.retry_advice(state, dry_run=False)
    assert notify_r3.advice_outbox(state).exists()
    assert not notify_r3.retry_advice(state, dry_run=True)
    assert notify_r3.advice_outbox(state).exists()


def test_invalid_ids_cannot_enter_the_acknowledgement_command(tmp_path: Path) -> None:
    state = tmp_path / "notify.json"
    notify_r3.queue_advice(state, "message", ["7; command"], dry_run=False)
    assert not notify_r3.advice_outbox(state).exists()


def test_advisor_dry_run_never_starts_a_paid_model_or_changes_the_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        message = "dry-run must not execute a container command"
        raise AssertionError(message)

    monkeypatch.setattr(notify_r3.subprocess, "run", unexpected)
    result = notify_r3.advisor("21", tmp_path / "state.json", dry_run=True, force=True)
    assert "dry-run" in result
    assert not notify_r3.advice_outbox(tmp_path / "state.json").exists()
