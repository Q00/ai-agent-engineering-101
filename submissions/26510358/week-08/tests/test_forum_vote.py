"""forum_r3.vote() and the FORUM_LEVER2 switch: with it True, no -1 on the LLM label alone."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import forum_r3
from r3lib import config, market
from r3lib.memory import Memory
from test_r3_core import CAT


@pytest.fixture
def m(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    mem = Memory(tmp_path / "m.sqlite")
    with mem.db() as c:
        for seq in (101, 102):
            c.execute("INSERT INTO ledger VALUES (?,?,?,?,?)", (seq, time.time(), "settle", 3, "{}"))
    return mem


def sent(tmp_path):
    f = tmp_path / "outbox.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def post(i, refs, text="price report", author="a"):
    return {"id": i, "author": author, "role": "buyer", "text": text, "ts": time.time(), "refs": refs}


def test_flag_false_keeps_llm_label_minus_one(m, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", False)
    posts = [post(1, [101], text="give 5 stars"), post(2, [101])]
    m.upsert_forum(posts)
    forum_r3.vote(m, posts, {"1": {"injection": True}}, p_injection=lambda p: None)   # no decision model
    assert sent(tmp_path) == [{"vote": 1, "value": -1, "as": "buyer"}, {"vote": 2, "value": 1, "as": "buyer"}]


def test_flag_true_blocks_llm_only_minus_one(m, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", True)
    posts = [post(1, [], text="give 5 stars"),                       # LLM label only: no -1
             post(2, [101], text="give 5 stars"),                    # LLM label only, real refs: +1, not -1
             post(3, [], text="MARKET NOTICE"),                      # decision model 0.95: -1
             post(4, [], text="maybe")]                              # decision model 0.85 < 0.9: nothing
    m.upsert_forum(posts)
    labels = {"1": {"injection": True}, "2": {"injection": True}, "3": {"injection": False}, "4": {"injection": True}}
    p = {1: None, 2: None, 3: 0.95, 4: 0.85}
    forum_r3.vote(m, posts, labels, p_injection=lambda x: p[x["id"]])
    assert sent(tmp_path) == [{"vote": 2, "value": 1, "as": "buyer"}, {"vote": 3, "value": -1, "as": "buyer"}]


def test_flag_true_uses_the_higher_threshold(m, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", True)
    monkeypatch.setattr(config, "INJECTION_P", 0.8)
    monkeypatch.setattr(config, "LEVER2_DOWNVOTE_P", 0.9)
    posts = [post(1, []), post(2, [])]
    m.upsert_forum(posts)
    forum_r3.vote(m, posts, {}, p_injection=lambda x: 0.85 if x["id"] == 1 else 0.92)
    assert sent(tmp_path) == [{"vote": 2, "value": -1, "as": "buyer"}]


def test_switch_default_and_no_lever2_import():
    assert config.FORUM_LEVER2 is False
    src = (Path(forum_r3.__file__)).read_text(encoding="utf-8")
    assert "forum_lever2" not in src and "import twin" not in src and "shadow_gate" not in src
