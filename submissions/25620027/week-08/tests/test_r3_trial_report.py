"""The host-side trial report reads our ledgers and files without any r3 import."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from deploy.vps import trial_report
from r3.events import EventLedger
from r3.model_client import kst_day
from r3.picture import Picture, ShopTrust, save_picture
from r3.policy import Policy, save_policy, save_tactical

if TYPE_CHECKING:
    from pathlib import Path


def test_trial_report_counts_calls_changes_policy_picture_and_kit_events(
    tmp_path: Path,
) -> None:
    data = tmp_path / "r3"
    data.mkdir()
    events = EventLedger(data / "r3-events.sqlite")
    since = time.time() - 60
    events.record_model_decision(
        role="strategy",
        model="claude-sonnet-5-5",
        latency_ms=3600,
        input_tokens=2800,
        output_tokens=400,
        cost_usd=0.0096,
        accepted=True,
        summary="s",
        input_sha256="x",
        day=kst_day(),
    )
    events.record_model_decision(
        role="trade",
        model="claude-haiku-5-5",
        latency_ms=1200,
        input_tokens=900,
        output_tokens=40,
        cost_usd=0.00011,
        accepted=True,
        summary="t",
        input_sha256="y",
        day=kst_day(),
    )
    events.record_policy_change(
        actor="claude-sonnet-5-5",
        layer="strategy",
        field="seller.markup_a",
        before=0.95,
        after=0.9,
        reason="test",
        expires_at=None,
        version=2,
        accepted=True,
    )
    events.record_attempt(
        order="o",
        shop="http://s",
        shop_name="S",
        sku="k",
        list_price=1,
        price=1,
        outcome="agreed",
    )
    _ = save_policy(data / "r3-policy.json", Policy(), "strategy")
    _ = save_tactical(data / "r3-policy.json", {"seller.concession": 0.33}, "slow")
    save_picture(
        data / "r3-picture.json",
        Picture(
            by="claude-haiku-5-5",
            shops=(ShopTrust(url="http://b", name="B", trust=0.2),),
        ),
    )
    log = tmp_path / "market-kit.log"
    _ = log.write_text(
        "10:00:00 [kit] market_kit 4\n10:00:05 [order o-1] x\n10:00:09 verified True\n"
        "10:05:00 [kit] market_kit 4\n10:05:05 [order o-2] y\n"
        "10:05:06 [buyer] [R3 BUYER] read intent=quote ask=5\n"
        "10:05:07 [seller] seller.screened task=t reason=authority_claim count=1\n",
        encoding="utf-8",
    )
    report = trial_report.summarize(data, since, log)
    assert {c["role"] for c in report["model_calls"]} == {"strategy", "trade"}
    assert report["spend_today_calls"] == 2
    assert report["policy_changes"][0]["field"] == "seller.markup_a"
    assert report["buyer_attempts"] == {"agreed": 1}
    assert report["policy"]["version"] == 3
    assert report["policy"]["tactical"]["changes"] == {"seller.concession": 0.33}
    assert report["picture"]["low_trust"] == [{"name": "B", "trust": 0.2, "flags": []}]
    # Only lines after the last kit banner count (the trial's own relaunch).
    assert report["kit_log"] == {
        "orders": 1,
        "verified": 0,
        "buyer_reads": 1,
        "seller_reads": 0,
        "screened": 1,
        "refused_openings": 0,
        "paid": 0,
        "not_paid": 0,
    }
    assert json.dumps(report, ensure_ascii=False)
