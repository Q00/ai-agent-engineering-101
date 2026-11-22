"""Watch: code finds the alerts, Sonnet only explains them, nobody edits policy."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import anyio

from deploy.vps import notify_r3
from deploy.vps.notify_r3 import Snapshot
from r3 import manager as manager_module
from r3 import watch
from r3.manager import Manager
from r3.model_client import ModelClient, ModelReply, Request

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    import pytest

NOW = 1_794_300_000.0
URL = "https://us.test"


def ledger(path: Path, rows: list[tuple[float, str, str, str, str, int, dict]]) -> Path:
    """Build a market ledger with the columns the real one has."""
    with sqlite3.connect(path) as db:
        _ = db.execute(
            """CREATE TABLE rows (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT,
            round INTEGER, buyer TEXT, shop TEXT, sku TEXT, amount INTEGER,
            grade TEXT, claim TEXT, claim_honest INTEGER, data TEXT)"""
        )
        _ = db.executemany(
            "INSERT INTO rows (ts, kind, buyer, shop, sku, amount, data) "
            "VALUES (?,?,?,?,?,?,?)",
            [(ts, k, b, s, sku, a, json.dumps(d)) for ts, k, b, s, sku, a, d in rows],
        )
    return path


def busy_day(
    price: int, last_hour: int
) -> list[tuple[float, str, str, str, str, int, dict]]:
    """30 buyer settles in each of the 24 earlier hours, then ``last_hour``."""
    rows = []
    for hour in range(1, 25):
        for n in range(30):
            ts = NOW - 3600 - hour * 3600 + n * 60 + 30
            rows.append(
                (ts, "settle", "me", "https://x", "G1", price, {"unit": f"u{hour}-{n}"})
            )
    rows += [
        (NOW - 60 - n, "settle", "me", "https://x", "G1", price, {"unit": f"l{n}"})
        for n in range(last_hour)
    ]
    return rows


def test_a_quiet_hour_and_low_credit_trip_their_lines(tmp_path: Path) -> None:
    # Given: 30 buyer deals an hour all day, then 5 in the last hour at 0.30 credit.
    db = ledger(tmp_path / "l.sqlite", busy_day(7000, 5))
    metrics = watch.collect(db, {"G1": 10000}, buyer="me", shop_url=URL, now=NOW)
    found = watch.checks(metrics, {"chain": "closed", "agent_md": "same"})
    # Then: the drop trips; 0.30 credit trips the 0.37 line (enough deals in 2 h).
    assert metrics["buyer_settles_1h"] == 5
    assert metrics["buyer_settles_hourly_median_24h"] == 30
    assert set(found) == {"buyer_drop", "credit_low"}


def test_a_normal_hour_raises_nothing(tmp_path: Path) -> None:
    # Given: the last hour matches the day at 0.40 credit.
    db = ledger(tmp_path / "l.sqlite", busy_day(6000, 30))
    metrics = watch.collect(db, {"G1": 10000}, buyer="me", shop_url=URL, now=NOW)
    assert watch.checks(metrics, {"chain": "closed", "agent_md": "same"}) == {}


def test_broken_goods_count_zero_if_refunded_even_when_listed_first(
    tmp_path: Path,
) -> None:
    # Given: a refunded break recorded before its settle row, and an unrefunded one.
    rows = [
        (
            NOW - 100,
            "broken",
            "me",
            "https://x",
            "G1",
            0,
            {"unit": "a", "refunded": 6000},
        ),
        (NOW - 90, "settle", "me", "https://x", "G1", 6000, {"unit": "a"}),
        (NOW - 80, "settle", "me", "https://x", "G1", 6000, {"unit": "b"}),
        (NOW - 70, "broken", "me", "https://x", "G1", 0, {"unit": "b", "refunded": 0}),
        (NOW - 60, "settle", "me", "https://x", "G1", 6000, {"unit": "c"}),
    ]
    db = ledger(tmp_path / "l.sqlite", rows)
    metrics = watch.collect(db, {"G1": 10000}, buyer="me", shop_url=URL, now=NOW)
    # Then: refunded 0, unrefunded -1, intact 0.4 -> mean -0.2.
    assert metrics["buyer_credit_2h"] == round((0.0 - 1.0 + 0.4) / 3, 4)


def test_low_paid_stars_and_round_four_signals_trip(tmp_path: Path) -> None:
    rows = [
        (
            NOW - 60 - n,
            "result",
            "b",
            URL + "/",
            "G1",
            0,
            {"stars": 3, "checks": {"settled": True}},
        )
        for n in range(watch.MIN_STAR_REVIEWS)
    ]
    db = ledger(tmp_path / "l.sqlite", rows)
    metrics = watch.collect(db, {"G1": 10000}, buyer="me", shop_url=URL, now=NOW)
    found = watch.checks(metrics, {"chain": "live", "agent_md": "changed"})
    assert set(found) == {"stars_low", "round4_chain", "agent_md_changed"}


def test_agent_md_change_needs_a_baseline_and_repeats_wait_three_hours() -> None:
    state, digest = watch.agent_md_state("v1", "")
    assert state == "same"
    assert watch.agent_md_state("v1", digest)[0] == "same"
    assert watch.agent_md_state("v2", digest)[0] == "changed"
    assert watch.agent_md_state(None, digest) == ("unknown", digest)
    found = {"a": "x", "b": "y"}
    assert watch.fresh_keys(found, {"a": NOW - 3600}, NOW) == ["b"]
    assert watch.fresh_keys(found, {"a": NOW - 3 * 3600}, NOW) == ["a", "b"]


def test_only_well_formed_explanations_are_kept() -> None:
    parsed = {
        "explanations": [
            {"key": "credit_low", "likely_cause": "c", "check_next": "n", "avoid": "a"},
            {"likely_cause": "no key"},
            "junk",
        ]
    }
    assert watch.explanations_by_key(parsed) == {
        "credit_low": {"likely_cause": "c", "check_next": "n", "avoid": "a"}
    }
    assert watch.explanations_by_key(None) == {}


@contextmanager
def managed(path: Path) -> Generator[Manager, None, None]:
    manager = Manager(
        path, "http://127.0.0.1:9", shop_url=URL, buyer_name="me", shop_name="me"
    )
    try:
        yield manager
    finally:
        manager.http.close()


def test_manager_writes_an_explained_alert_once_and_edits_no_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a tripped check and a stubbed Sonnet that explains it.
    with managed(tmp_path) as manager:
        monkeypatch.setattr(
            watch,
            "collect",
            lambda *_a, **_k: {"buyer_credit_2h": 0.3, "buyer_credit_deals_2h": 40.0},
        )
        monkeypatch.setattr(
            manager, "market_signals", lambda: {"chain": "closed", "agent_md": "same"}
        )
        client = ModelClient("fake", events=manager.events, quota=None)
        seen: list[Request] = []

        async def call(request: Request) -> ModelReply:
            seen.append(request)
            body = {
                "explanations": [
                    {
                        "key": "credit_low",
                        "likely_cause": "가격 상승",
                        "check_next": "가게별 가격",
                        "avoid": "기준 하향",
                    }
                ]
            }
            return ModelReply(json.dumps(body), "claude-sonnet-5-5", 1, 1, 1, 0)

        monkeypatch.setattr(client, "call", call)
        monkeypatch.setattr(manager, "client", lambda: client)
        before = manager.policy_file.exists()
        # When: two watch passes run within three hours.
        first = anyio.run(manager.watch, NOW)
        second = anyio.run(manager.watch, NOW + 1800)
        # Then: one alert line with Sonnet's explanation; no repeat; no policy file.
        assert first == ["credit_low"]
        assert second == []
        assert len(seen) == 1
        assert seen[0].tool_name == "explain_alerts"
        assert "facts" in json.loads(seen[0].user)
        line = json.loads((tmp_path / watch.WATCH_FILE).read_text().splitlines()[0])
        assert line["key"] == "credit_low"
        assert line["explanation"]["avoid"] == "기준 하향"
        assert manager.policy_file.exists() == before


def test_tick_no_longer_calls_the_policy_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: every job is due.
    assert manager_module.MODEL_POLICY_EDITS is False
    with managed(tmp_path) as manager:
        called: list[str] = []

        async def tactical() -> None:
            called.append("tactical")

        async def ask_model(_summary: object) -> None:
            called.append("strategy")

        async def watched(_now: float) -> list[str]:
            called.append("watch")
            return []

        async def scout(*, use_model: bool = True) -> object:
            _ = use_model
            return None

        monkeypatch.setattr(manager, "tactical", tactical)
        monkeypatch.setattr(manager, "ask_model", ask_model)
        monkeypatch.setattr(manager, "watch", watched)
        monkeypatch.setattr(manager, "scout", scout)
        monkeypatch.setattr(manager, "pull_ledger", lambda: 0)
        monkeypatch.setattr(manager, "refresh_buyer_report", lambda: None)
        monkeypatch.setattr(manager, "refresh_shop_names", lambda: None)
        monkeypatch.setattr(manager, "post_forum", list)
        monkeypatch.setattr(manager, "benchmark", dict)

        def best_sellers() -> list[dict[str, object]]:
            called.append("best_sellers")
            return []

        monkeypatch.setattr(manager, "best_sellers", best_sellers)

        async def digest() -> dict[str, object]:
            return {}

        monkeypatch.setattr(manager, "review_digest", digest)
        # When: one tick runs.
        anyio.run(manager.tick, time.time())
        # Then: the watch runs and restock's inputs are refreshed; tactical and
        # strategy policy calls do not run.
        assert called == ["best_sellers", "watch"]


def test_notifier_sends_watch_alerts_in_quiet_hours_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the owner's quiet-loops switch and one fresh explained alert.
    quiet = tmp_path / "quiet"
    quiet.touch()
    monkeypatch.setattr(notify_r3, "QUIET_LOOPS", quiet)
    alert = {
        "ts": NOW - 60,
        "key": "credit_low",
        "message": "구매 거래당 기여가 낮습니다.",
        "explanation": {
            "likely_cause": "가격 상승",
            "check_next": "가게별",
            "avoid": "기준 하향",
        },
    }
    snap = Snapshot(now=NOW, watch_alerts=[alert])
    state: dict[str, object] = {"watch_cursor": NOW - 600}
    # When: the notifier decides twice.
    first = notify_r3.watch_messages(snap, state)
    second = notify_r3.watch_messages(snap, state)
    # Then: one message with the explanation, not repeated.
    assert len(first) == 1
    assert "원인 추정: 가격 상승" in first[0]
    assert "하지 말 것: 기준 하향" in first[0]
    assert second == []
