"""The watcher decides alerts, repeats, recoveries and one-time notices correctly."""

from __future__ import annotations

from datetime import UTC, datetime

from deploy.vps import notify_r3
from deploy.vps.notify_r3 import Snapshot, conditions, decide


def healthy(now: float) -> Snapshot:
    return Snapshot(
        now=now,
        container_up=True,
        kit_age=20,
        manager_on=True,
        stop_at="2026-11-17T23:59:59+09:00",
        switch_done=True,
        market_round=3,
        settles_1h_market=50,
        shop_settles_1h=2,
        buyer_settles_1h=3,
        shop_row={"profit": 1000, "revenue": 5000, "stars": 4.5, "deals": 30},
        buyer_row={"saved": 9000, "deals": 40},
        shop_rank="3/50",
        buyer_rank="5/60",
    )


def test_healthy_run_after_the_switch_raises_nothing_but_one_time_notices() -> None:
    now = datetime(2026, 11, 3, 1, 0, tzinfo=UTC).timestamp()  # 10:00 KST
    snap = healthy(now)
    assert conditions(snap) == set()
    messages, state = decide(snap, {})
    assert any("라운드3가 열렸습니다" in m for m in messages)
    assert any("전환 완료" in m for m in messages)
    assert state["once"] == ["round3", "switch"]
    # And: the next pass sends nothing.
    again, _ = decide(healthy(now + 300), state)
    assert again == []


def test_alerts_repeat_every_two_hours_and_recover_once() -> None:
    now = datetime(2026, 11, 4, 3, 0, tzinfo=UTC).timestamp()
    state: dict[str, object] = {"once": ["round3", "switch"]}
    snap = healthy(now)
    snap.container_up = False
    snap.kit_age = 1200
    messages, state = decide(snap, state)
    assert len(messages) == 2
    assert all("Claude 세션에서 확인" in m for m in messages)
    later = healthy(now + 600)
    later.container_up = False
    later.kit_age = 1800
    messages, state = decide(later, state)
    assert messages == []  # within two hours: no repeat
    messages, state = decide(healthy(now + 900), state)
    assert len(messages) == 2
    assert all("회복" in m for m in messages)
    assert state["alerts"] == {}


def test_switch_alerts_wait_for_round_three_and_a_grace_period() -> None:
    snap = healthy(datetime(2026, 11, 3, 1, 0, tzinfo=UTC).timestamp())
    snap.manager_on, snap.switch_done, snap.stop_at = False, False, "2026-11-03T23:59"
    snap.market_round = 2
    assert "switch_missing" not in conditions(snap)
    snap.market_round = 3
    messages, state = decide(snap, {"once": ["round3"]})
    assert not any("10분 넘게 전환" in m for m in messages)  # inside the grace period
    later = healthy(snap.now + 700)
    later.manager_on, later.switch_done, later.stop_at = (
        False,
        False,
        "2026-11-03T23:59",
    )
    messages, _ = decide(later, state)
    assert any("10분 넘게 전환" in m for m in messages)


def test_stop_time_risk_after_ten_thirty_pm() -> None:
    snap = healthy(datetime(2026, 11, 3, 13, 40, tzinfo=UTC).timestamp())
    snap.market_round, snap.stop_at = 2, "2026-11-03T23:59:00+09:00"
    assert "stop_at_risk" in conditions(snap)
    snap.stop_at = "2026-11-17T23:59:59+09:00"
    assert "stop_at_risk" not in conditions(snap)


def test_no_sales_cost_and_billing_conditions() -> None:
    snap = healthy(datetime(2026, 11, 5, 3, 0, tzinfo=UTC).timestamp())
    snap.shop_settles_1h = snap.buyer_settles_1h = 0
    snap.spend_today, snap.daily_cap = 3.2, 3.0
    snap.cooldown = True
    assert conditions(snap) == {"no_sales", "spend_cap", "billing"}
    snap.market_round = 2
    assert "no_sales" not in conditions(snap)


def test_daily_summary_once_at_nine_and_final_notice_at_the_end(monkeypatch) -> None:  # noqa: ANN001
    nine = datetime(2026, 11, 6, 0, 25, tzinfo=UTC).timestamp()  # 09:25 KST (fallback)
    state: dict[str, object] = {"once": ["round3", "switch"]}
    messages, state = decide(healthy(nine), state)
    assert len(messages) == 1
    assert "아침 요약" in messages[0]
    assert "가게 3/50위" in messages[0]
    messages, state = decide(healthy(nine + 600), state)
    assert messages == []
    end = notify_r3.ROUND_END.timestamp() + 60
    messages, _ = decide(healthy(end), state)
    assert any("라운드3 종료" in m for m in messages)
    _ = monkeypatch


def test_vault_job_matches_the_worker_contract(tmp_path) -> None:  # noqa: ANN001
    body = notify_r3.as_markdown(
        "[AX R3] 자문 보고 21시\n\n■ 결정 필요 1건\n\n#3 [재입고 · 작업 S] 주기 단축\n근거: 품절 8회",  # noqa: E501
        "2026-11-03 21시",
    )
    assert body.startswith("### 2026-11-03 21시 자문 보고 21시")
    assert "- **#3 [재입고 · 작업 S] 주기 단축**" in body
    path = notify_r3.vault_job(
        "## 자문 보고",
        body,
        "ax-r3-advisor-2026-11-03-21",
        dry_run=False,
        jobs=tmp_path,
    )
    assert path is not None
    assert "MAC_WORKER_APPROVED_OBSIDIAN" in path.name
    text = path.read_text(encoding="utf-8")
    assert "type: obsidian_update_request" in text
    assert "operation: append_after_heading" in text
    assert f'target: "{notify_r3.VAULT_NOTE}"' in text
    assert 'heading: "## 자문 보고"' in text
    assert "  - 근거: 품절 8회" in text  # block literal stays indented
    assert 'idempotency_key: "ax-r3-advisor-2026-11-03-21"' in text
    assert (
        notify_r3.vault_job("## 자문 보고", "  ", "k", dry_run=False, jobs=tmp_path)
        is None
    )


def runs_before(now: float) -> list[tuple[float, str, str, str]]:
    return [
        (now - 900, "scout", "read", "shops=12 low_trust=2 hot=식품 alerts=0"),
        (now - 600, "tactical", "held", "close rate normal"),
        (now - 60, "tactical", "nudged", '{"buyer.quick_accept_ratio": 0.756} trigger'),
    ]


def test_policy_changes_go_out_once_and_loops_report_every_hour() -> None:
    now = datetime(2026, 11, 4, 3, 30, tzinfo=UTC).timestamp()  # 12:30 KST
    state: dict[str, object] = {"once": ["round3", "switch"]}
    snap = healthy(now)
    snap.loop_runs = runs_before(now)
    snap.policy_changes = [
        (
            now - 60,
            "claude-haiku-5-5",
            "tactical",
            "buyer.quick_accept_ratio",
            "0.72",
            "0.75",
            "buyer_walkaway (agreed 66/124 priced orders in 1h)",
        )
    ]
    # When: the first pass after a change runs.
    messages, state = decide(snap, state)
    # Then: the change goes out once, in plain Korean with the number behind it;
    # the first pass only starts the hourly clock.
    assert len(messages) == 1
    assert "정책 변경" in messages[0]
    assert "바이어 즉시 수락선: 72% → 75%" in messages[0]
    assert "바이어 합의율 낮음(1시간 66/124)" in messages[0]
    again = healthy(now + 300)
    again.loop_runs = runs_before(now)
    again.policy_changes = snap.policy_changes
    assert decide(again, state)[0] == []
    # When: the next clock hour starts (13:02 KST) with the same runs in its hour.
    later = healthy(now + 1920)
    later.loop_runs = runs_before(now)
    messages, state = decide(later, state)
    # Then: one digest with counts, the change and the hold reason.
    assert len(messages) == 1
    digest = messages[0]
    assert "전술 2회" in digest
    assert "유지 1" in digest
    assert "close rate normal" in digest
    assert "정찰 최근" in digest


def test_silent_loops_alert_only_after_the_switch() -> None:
    now = datetime(2026, 11, 4, 3, 0, tzinfo=UTC).timestamp()
    snap = healthy(now)
    assert "loops_silent" not in conditions(snap)  # older release: no table
    snap.loop_runs = [(now - 3000, "scout", "read", "")]
    assert "loops_silent" in conditions(snap)
    snap.manager_on = False
    assert "loops_silent" not in conditions(snap)


def test_silent_loops_wait_forty_minutes_after_the_switch() -> None:
    now = datetime(2026, 11, 3, 12, 5, tzinfo=UTC).timestamp()
    snap = healthy(now)
    snap.loop_runs = []  # the switch just relaunched; no loop has run yet
    snap.switched_at = now - 120
    assert "loops_silent" not in conditions(snap)
    snap.switched_at = now - 3000
    assert "loops_silent" in conditions(snap)


def test_engineer_notes_go_out_once() -> None:
    now = datetime(2026, 11, 4, 3, 30, tzinfo=UTC).timestamp()
    state: dict[str, object] = {"once": ["round3", "switch"]}
    snap = healthy(now)
    snap.engineer_notes = [
        (now - 60, "buyer picks 39,900 list items while leaders pick 87,900")
    ]
    messages, state = decide(snap, state)
    assert len(messages) == 1
    assert "엔지니어 메모" in messages[0]
    assert "87,900" in messages[0]
    again = healthy(now + 300)
    again.engineer_notes = snap.engineer_notes
    assert decide(again, state)[0] == []
