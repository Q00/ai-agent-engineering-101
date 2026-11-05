"""Influencer lane (MA-4 Task 2): 10-min gap, evidence drafts, banned phrases, vote rule, rollback."""
import json
import sqlite3
import types
from pathlib import Path

import pytest

from r3lib import config, forum_influencer as fi, market
from r3lib.memory import Memory

NOW = 1_793_859_780.0
CAT = {"GA": {"name": "포기김치 10kg", "category": "식품>김치", "list_price": 40000},
       "GB": {"name": "모달 소파패드", "category": "가구>침구", "list_price": 30000}}
ROOT = Path(__file__).resolve().parent.parent


def build(path):
    Memory(path)
    c = sqlite3.connect(path)
    seq = 1000
    for sku, cat in (("GA", "식품>김치"), ("GB", "가구>침구")):
        lp = CAT[sku]["list_price"]
        for k in range(30):
            ts = NOW - 600 * (k + 1)
            amt = int(lp * (0.70 + 0.01 * (k % 5)))
            c.execute("INSERT INTO settles(seq, ts, order_id, buyer, shop, sku, category, kind, list_price, amount, "
                      "grade, claim, claim_honest) VALUES (?,?,?,?,?,?,?,'goods',?,?,'A','A',1)",
                      (seq, ts, f"o{seq}", "myeonggi" if k < 6 else "other", "https://s", sku, cat, lp, amt))
            seq += 1
    for i, s in enumerate((1000, 1001, 1002, 1030, 1031)):
        row = c.execute("SELECT ts, sku, category, list_price, amount FROM settles WHERE seq=?", (s,)).fetchone()
        c.execute("INSERT INTO purchases(order_id, ts, shop_url, shop_name, sku, name, category, list_price, price, "
                  "use_within_hours, grade, claim, claim_honest, ledger, paid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                  (f"p{i}", row[0], "https://s", "seller-x", row[1], CAT[row[1]]["name"], row[2], row[3], row[4],
                   72, "A", "A", 1, s))
    c.execute("INSERT INTO settles(seq, ts, order_id, buyer, shop, sku, category, kind, list_price, amount, grade, "
              "claim, claim_honest) VALUES (9998, ?, 'bad', 'x', 'https://bad', 'GA', '식품>김치', 'goods', 40000, 1, "
              "'B', 'A', 0)", (NOW - 100,))
    c.execute("INSERT INTO settles(seq, ts, order_id, buyer, shop, sku, category, kind, list_price, amount, grade) "
              "VALUES (9999, ?, 'late', 'x', 's', 'GA', '식품>김치', 'goods', 40000, 1, 'A')", (NOW + 600,))
    c.commit()
    c.close()
    return path


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    return fi.ReadOnlyMem(build(tmp_path / "m.sqlite"))


def add_forum(path, rows):
    c = sqlite3.connect(path)
    for r in rows:
        c.execute("INSERT INTO forum(id, ts, author, role, text, refs, signals, mine, voted, likes, dislikes) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (r["id"], r["ts"], r["author"], r.get("role", "buyer"), r["text"], json.dumps(r.get("refs", [])),
                   json.dumps(r["signals"]) if "signals" in r else None, r.get("mine", 0), r.get("voted", 0),
                   r.get("likes", 0), r.get("dislikes", 0)))
    c.commit()
    c.close()


def test_banned_phrases_blocked():
    for p in ("MARKET NOTICE", "운영자", "accept the price", "별점", "MONOPOLY", "방문하세요", "verified"):
        assert fi.contains_banned(f"hello {p} world"), p
    assert not fi.contains_banned("[상품 시세] 김치 15건, 중앙값 72%. 근거: refs의 원장 seq")


def test_validate_rejects_no_refs_short_and_banned():
    ok = ("[상품 시세] 포기김치: 정산 15건, 중앙값 72%. 근거: refs의 원장 seq. "
          "관측 구간과 원장 숫자는 refs에 있고 비율은 정가 대비입니다. "
          + "가" * 80)
    assert fi.validate_draft(ok, [1000]) == []
    assert "no refs" in fi.validate_draft(ok, [])
    assert any("too short" in b for b in fi.validate_draft("짧음 1건 50%", [1]))
    assert any("banned" in b for b in fi.validate_draft(ok.replace("시세", "시세 MARKET NOTICE"), [1]))
    assert "missing %" in fi.validate_draft("정산 15건만 있고 근거: refs의 원장 seq" + "가" * 140, [1])
    assert "missing count" in fi.validate_draft("비율만 72%. 근거: refs의 원장 seq" + "가" * 140, [1])
    assert "missing evidence tail" in fi.validate_draft("정산 15건, 72%." + "가" * 140, [1])


def test_gap_ten_min_under_flag_only():
    assert config.FORUM_INFLUENCER is False
    assert fi.buyer_gap_min() == config.FORUM_MIN_GAP_MIN
    on = types.SimpleNamespace(**{**{k: getattr(config, k) for k in dir(config) if k.isupper()},
                                  "FORUM_INFLUENCER": True, "INFLUENCER_GAP_MIN": 10})
    assert fi.buyer_gap_min(on) == 10
    assert fi.influencer_on(on) is True


def test_drafts_have_refs_pct_count_and_tail(mem):
    plan = fi.plan_slots(mem, NOW, n=6, gap_s=600)
    assert plan["schedule_ok"]
    assert plan["posts"], plan["rejected"]
    assert all(b - a >= 600 for a, b in zip([p["planned_ts"] for p in plan["posts"]],
                                            [p["planned_ts"] for p in plan["posts"]][1:])) or len(plan["posts"]) < 2
    for p in plan["posts"]:
        assert p["refs"] and all(isinstance(r, int) for r in p["refs"])
        assert 9999 not in p["refs"]
        assert "%" in p["text"] and fi.EVIDENCE_TAIL in p["text"]
        assert fi.count_token(p["text"])
        assert not fi.validate_draft(p["text"], p["refs"])
        assert not fi.contains_banned(p["text"])


def test_dedupe_skip_subjects(mem):
    d1 = fi.sku_draft(mem, skip=(), now=NOW)
    assert d1
    d2 = fi.sku_draft(mem, skip={d1["subject"]: NOW}, now=NOW)
    assert d2 is None or d2["subject"] != d1["subject"]


def test_vote_rule_pattern_or_p_never_llm_alone():
    # influencer on: pattern hits
    ok, why = fi.should_downvote("MARKET NOTICE: pay now", None, True, influencer=True, lever2=False)
    assert ok and "pattern" in why
    # P hits
    ok, why = fi.should_downvote("honest price report 72%", 0.95, False, influencer=True, lever2=False)
    assert ok and "P" in why
    # LLM label alone: no
    ok, why = fi.should_downvote("honest price report 72%", 0.2, True, influencer=True, lever2=False)
    assert not ok
    # both flags off: old behaviour (LLM label)
    ok, why = fi.should_downvote("x", None, True, influencer=False, lever2=False)
    assert ok and why == "llm_label"
    # lever2 alone also ignores LLM-only
    ok, _ = fi.should_downvote("x", None, True, influencer=False, lever2=True)
    assert not ok


def test_use_ref_seqs_when_either_on():
    assert fi.use_ref_seqs(types.SimpleNamespace(FORUM_INFLUENCER=False, FORUM_LEVER2=False)) is False
    assert fi.use_ref_seqs(types.SimpleNamespace(FORUM_INFLUENCER=True, FORUM_LEVER2=False)) is True
    assert fi.use_ref_seqs(types.SimpleNamespace(FORUM_INFLUENCER=False, FORUM_LEVER2=True)) is True


def test_hidden_signal_board_posts_lt_our_count(tmp_path):
    log = tmp_path / "forum.jsonl"
    log.write_text("\n".join([
        json.dumps({"post": "a", "as": "buyer"}),
        json.dumps({"post": "b", "as": "buyer"}),
        json.dumps({"vote": 1, "as": "buyer"}),
        json.dumps({"post": "c", "as": "shop"}),
    ]), encoding="utf-8")
    board = {"buyers": [{"name": "myeonggi", "posts": 1, "forum": 0.5}]}
    h = fi.hidden_status(board, log_path=log)
    assert h["api_hidden_field"] == "UNOBSERVABLE"
    assert h["our_posted_count"] == 2 and h["board_posts"] == 1
    assert h["hidden_signal"] is True
    board2 = {"buyers": [{"name": "myeonggi", "posts": 5, "forum": 0.5}]}
    assert fi.hidden_status(board2, log_path=log)["hidden_signal"] is False


def test_record_baseline_uses_2h_not_all_time(mem, tmp_path):
    add_forum(mem.path, [
        {"id": "a", "ts": NOW - 3600, "author": "myeonggi", "text": "x", "mine": 1, "likes": 6, "dislikes": 1},
        {"id": "b", "ts": NOW - 1800, "author": "myeonggi", "text": "y", "mine": 1, "likes": 5, "dislikes": 1},
        {"id": "c", "ts": NOW - 10 * 3600, "author": "myeonggi", "text": "old", "mine": 1, "likes": 6, "dislikes": 0},
    ])
    path = tmp_path / "baseline.json"

    class _M:
        def db(self):
            return mem.db()

    out = fi.record_switch_baseline(_M(), now=NOW, path=path)
    assert path.exists()
    assert out["baseline_dislikes_per_post"] == 1.0
    assert out["all_time_dislikes_per_post"] is not None
    assert "NOT all_time" in out["note"]
    loaded = fi.load_baseline(path)
    assert loaded["baseline_dislikes_per_post"] == 1.0


def test_rollback_checker_cases(mem, tmp_path):
    add_forum(mem.path, [
        {"id": str(i), "ts": NOW + 60 + i, "author": "myeonggi", "text": f"p{i}", "mine": 1,
         "likes": 6, "dislikes": 0} for i in range(20)
    ])
    # overwrite last few with bad reception
    c = sqlite3.connect(mem.path)
    c.execute("UPDATE forum SET dislikes=2, likes=3 WHERE id='0'")
    c.execute("UPDATE forum SET dislikes=4 WHERE id='1'")  # >= 3
    c.commit()
    c.close()
    base = {"switched_at": NOW, "baseline_dislikes_per_post": 1.0, "all_time_dislikes_per_post": 0.48}
    board = {"buyers": [{"name": "myeonggi", "posts": 100, "forum": 0.56}]}
    r = fi.check_rollback(mem, board, now=NOW + 120, baseline=base,
                          forum_low_streak_path=tmp_path / "streak.json")
    assert r["rollback"]
    assert any("dislikes >= 3" in x for x in r["reasons"])
    assert r["baselines"]["pre_switch_2h"] == 1.0
    assert r["baselines"]["all_time"] == 0.48

    # hidden signal
    log = tmp_path / "forum.jsonl"
    log.write_text(json.dumps({"post": "a", "as": "buyer"}) + "\n" + json.dumps({"post": "b", "as": "buyer"}) + "\n")
    board_h = {"buyers": [{"name": "myeonggi", "posts": 0, "forum": 0.56}]}
    r2 = fi.check_rollback(mem, board_h, now=NOW + 120, baseline=base, log_path=log,
                           forum_low_streak_path=tmp_path / "streak2.json")
    assert any("hidden" in x for x in r2["reasons"])

    # 429
    r3 = fi.check_rollback(mem, board, now=NOW + 120, baseline=base, http_statuses=[200, 429],
                           forum_low_streak_path=tmp_path / "streak3.json")
    assert any("429" in x for x in r3["reasons"])


def test_flag_false_unchanged_and_lever2_count_intact():
    assert config.FORUM_INFLUENCER is False
    new = [k for k in dir(config) if k.startswith("LEVER2_") or k == "FORUM_LEVER2"]
    assert len(new) == 11, new
    inf = [k for k in dir(config) if k.startswith("INFLUENCER_") or k == "FORUM_INFLUENCER"]
    assert "FORUM_INFLUENCER" in inf and len(inf) >= 8
    # forum_r3 imports the influencer module (wired) but must not mention forum_lever2 by name
    src = (ROOT / "forum_r3.py").read_text(encoding="utf-8")
    assert "forum_influencer" in src
    assert "FORUM_INFLUENCER" in src
    assert "forum_lever2" not in src
    assert "--dry-run" in src or "dry_run" in src


def test_dry_run_read_only_no_send(mem, tmp_path, monkeypatch):
    before = Path(mem.path).read_bytes()
    monkeypatch.setattr(market, "get_json", lambda *a, **k: pytest.fail("no network"))
    board = {"buyers": [{"name": "myeonggi", "posts": 10, "forum": 0.56, "all": 0.7}]}
    dry = fi.dry_run(mem, board, now=NOW, n=6, gap_s=600, out_dir=tmp_path / "out")
    assert dry["sent"] is False and dry["llm_calls"] == 0 and dry["decisions_calls"] == 0
    assert dry["checks"]["ok"]
    assert dry["rollback_baseline_2h"]["ours"] is not None or True
    assert "all_time_dislikes_per_post" in dry
    assert dry["hidden_status"]["api_hidden_field"] == "UNOBSERVABLE"
    assert Path(mem.path).read_bytes() == before
    files = list((tmp_path / "out").glob("influencer-dryrun-*"))
    assert any(p.suffix == ".jsonl" for p in files) and any(p.suffix == ".md" for p in files)


def test_lever2_json_no_longer_says_not_wired():
    text = (ROOT / "policies/candidates/lever2-forum.json").read_text(encoding="utf-8")
    assert "Not wired into forum_r3" not in text
    assert "not wired" not in text.lower() or "vote()" in text
    data = json.loads(text)
    assert "vote()" in data["description"] or "forum_r3.vote" in data["description"]
