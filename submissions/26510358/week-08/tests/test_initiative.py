"""Horvitz gate and the proactive buyer post (scripted ReAct, no network)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import forum_r3
from r3lib import config, initiative, market
from r3lib.memory import Memory
from test_r3_core import CAT, settle


def test_threshold_rises_with_dislikes_and_recent_posts():
    fresh, _ = initiative.speak_threshold({"dislike_share": 0.25}, 0)
    disliked, _ = initiative.speak_threshold({"dislike_share": 0.7}, 0)
    chatty, _ = initiative.speak_threshold({"dislike_share": 0.25}, 4)
    assert abs(fresh - 1.0 / (1.0 + initiative.BENEFIT)) < 1e-9          # 0.4 with the post-count benefit
    assert disliked > fresh and chatty > fresh and max(disliked, chatty) <= initiative.MAX_THRESHOLD


def test_valid_post_rejects_unknown_refs_and_threads():
    ok = {"text": "최근 12시간 ledger 기준 정직 등급 비율 요약입니다.", "refs": [5]}
    assert initiative.valid_post(ok, {5}, set())[1] == [5]
    assert isinstance(initiative.valid_post({**ok, "refs": [99]}, {5}, set()), str)
    assert isinstance(initiative.valid_post({**ok, "thread": "p9"}, {5}, set()), str)
    assert isinstance(initiative.valid_post({**ok, "text": "짧음"}, {5}, set()), str)


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    m = Memory(tmp_path / "m.sqlite")
    now = time.time()
    m._ingest([settle(i, "F1", 8000, now - i * 60) for i in range(1, 8)])
    return m, now


def outbox_lines(tmp_path):
    f = tmp_path / "outbox.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_posts_when_p_beats_threshold(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    final = {"post": True, "p_wanted": 0.7, "text": "생수 2L 최근 정산가는 정가의 80% 수준입니다(ledger).", "refs": [1, 2]}
    rec = forum_r3.proactive_buyer_post(m, now, run=lambda *a, **k: (final, [{}]))
    assert rec["decision"] == "post" and rec["source"] == "react"
    assert outbox_lines(tmp_path)[0]["refs"] == [1, 2]
    again = forum_r3.proactive_buyer_post(m, now + 60, run=lambda *a, **k: (final, [{}]))
    assert again["decision"] == "wait"                                  # min gap holds


def test_silent_when_p_low_or_nothing_new(tmp_path, monkeypatch):
    m, now = setup(tmp_path, monkeypatch)
    calls = []

    def run(*a, **k):
        calls.append(1)
        return {"post": True, "p_wanted": 0.3, "text": "x" * 40, "refs": [1]}, [{}]
    rec = forum_r3.proactive_buyer_post(m, now, run=run)
    assert rec["decision"] == "silent" and "below p*" in rec["why"]
    assert forum_r3.proactive_buyer_post(m, now, run=run)["why"].startswith("nothing new")
    assert len(calls) == 1 and outbox_lines(tmp_path) == []
