"""The read-only analyst raises flags from numbers alone; the model only explains."""

from __future__ import annotations

from r3.analyst import flags, note_schema, telegram_text


def digest(**board: object) -> dict[str, object]:
    """Build a steady digest: second place, enough buys at a good share."""
    base: dict[str, object] = {
        "rank": 2,
        "market_saved_median": 0.72,
        "board_keys": ["buyers", "players", "shops"],
        "top8": {"yms979": 1.767, "SungJinho": 1.664},
    }
    base.update(board)
    return {
        "board": base,
        "ledger": {
            "buys_last_h": {"count": 80, "share_mean": 0.87},
            "sales_last_h": 30,
            "sales_24h_median_per_h": 40,
        },
        "logs": {"traceback": 0},
    }


def test_a_steady_hour_raises_nothing() -> None:
    assert flags(digest(), digest()) == []


def test_a_rule_change_a_new_leader_and_a_rival_jump_are_flagged() -> None:
    now = digest(
        rank=1,
        market_saved_median=0.80,
        top8={"yms979": 1.80, "SungJinho": 1.81},
    )
    raised = {f["key"]: f["level"] for f in flags(now, digest())}
    assert raised["scoring_change"] == "urgent"
    assert raised["rank_move"] == "urgent"
    assert raised["rival_jump:yms979"] == "watch"


def test_our_own_trouble_is_urgent_but_a_sales_dip_only_watched() -> None:
    now = digest()
    now["ledger"] = {
        "buys_last_h": {"count": 30, "share_mean": 0.70},
        "sales_last_h": 10,
        "sales_24h_median_per_h": 40,
    }
    now["logs"] = {"traceback": 2}
    raised = {f["key"]: f["level"] for f in flags(now, digest())}
    assert raised == {
        "buys_low": "urgent",
        "share_low": "urgent",
        "sales_low": "watch",
        "traceback": "urgent",
    }


def test_the_phone_message_leads_with_flags_then_the_note() -> None:
    text = telegram_text(
        {"headline": "1위와 차이가 줄었습니다.", "findings": ["a", "b", "c", "d"]},
        [{"key": "x", "level": "urgent", "text": "구매 30건"}],
        0.0,
    )
    lines = text.splitlines()
    assert lines[0].startswith("🧠 AX 분석가")
    assert lines[1] == "⚠ 구매 30건"
    assert "1위와 차이가 줄었습니다." in lines
    assert "• d" not in lines
    assert note_schema()["required"] == ["headline", "findings", "checks", "urgency"]
