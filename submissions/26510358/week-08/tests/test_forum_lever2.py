"""lever2-forum (shadow only): 5-min gap, unique posts with numbers and real refs, fair votes, rollback, switch."""
import json
import sqlite3
import types
from pathlib import Path

import pytest

from r3lib import config, forum_lever2 as fl, market
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
        for k in range(30):                                     # 15 in the last 3 h, 15 in the 3 h before
            ts = NOW - 600 * (k + 1)
            amt = int(lp * (0.70 + 0.01 * (k % 5)))
            c.execute("INSERT INTO settles(seq, ts, order_id, buyer, shop, sku, category, kind, list_price, amount, "
                      "grade) VALUES (?,?,?,?,?,?,?,'goods',?,?,'A')",
                      (seq, ts, f"o{seq}", "myeonggi" if k < 6 else "other", "https://s", sku, cat, lp, amt))
            seq += 1
    for i, s in enumerate((1000, 1001, 1002, 1030, 1031)):       # our paid purchases, ledger in settles
        row = c.execute("SELECT ts, sku, category, list_price, amount FROM settles WHERE seq=?", (s,)).fetchone()
        c.execute("INSERT INTO purchases(order_id, ts, shop_url, shop_name, sku, name, category, list_price, price, "
                  "use_within_hours, grade, claim, claim_honest, ledger, paid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                  (f"p{i}", row[0], "https://s", "seller-x", row[1], CAT[row[1]]["name"], row[2], row[3], row[4],
                   72, "A", "A", 1, s))
    c.execute("INSERT INTO settles(seq, ts, order_id, buyer, shop, sku, category, kind, list_price, amount, grade) "
              "VALUES (9999, ?, 'late', 'x', 's', 'GA', '식품>김치', 'goods', 40000, 1, 'A')", (NOW + 600,))
    c.commit()
    c.close()
    return path


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    return fl.ReadOnlyMem(build(tmp_path / "m.sqlite"))


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


# ------------------------------------------------------------------------------------------- posts
def test_dry_run_plans_twelve_posts_300_s_apart(mem, monkeypatch):
    plan = fl.plan_posts(mem, NOW, n=12, gap_s=300, history=[])
    ts = [p["planned_ts"] for p in plan["posts"]]
    assert len(ts) >= 5 and plan["schedule_ok"]                 # small fixture: near-duplicates are dropped
    assert all(b - a == 300 for a, b in zip(ts, ts[1:]))
    assert fl.check_schedule([0, 300, 600]) and not fl.check_schedule([0, 299])

    words = ["사과", "배추", "소파", "이불", "생수", "감자", "견과", "커피", "라면", "김치", "매트", "베개"]

    def many(_mem, _now, t):
        for i, w in enumerate(words):
            yield {"topic": t, "subject": f"{t}:{w}", "refs": [1000 + i],
                   "text": f"{w} {t} {i * 1_111 + 7:,}원 {50 + i}% 관측 {w * (i + 1)}"}
    monkeypatch.setattr(fl, "DRAFTERS", {t: (lambda m, n, t=t: many(m, n, t)) for t in fl.TOPICS})
    full = fl.plan_posts(mem, NOW, n=12, gap_s=300, history=[])
    assert [p["planned_ts"] - NOW for p in full["posts"]] == [300 * k for k in range(12)]
    assert full["schedule_ok"]


def test_every_post_has_numbers_and_real_ledger_refs(mem):
    plan = fl.plan_posts(mem, NOW, n=12, history=[])
    held = fl.ledger_seqs(mem)
    for p in plan["posts"]:
        assert len(fl.numbers(p["text"])) >= fl.MIN_NUMBERS
        assert p["refs"] and all(isinstance(r, int) and r in held for r in p["refs"])
        assert 9999 not in p["refs"]                            # nothing after the as-of time
    topics = {p["topic"] for p in plan["posts"]}
    assert topics == {"purchase", "category", "trend"}


def test_purchase_post_carries_price_vs_list_grade_seller_and_ledger(mem):
    d = next(fl.purchase_drafts(mem, NOW))
    for part in ("[구매 기록]", "정가", "등급 A", "사용 기한 조건 72h", "판매: seller-x", f"ledger #{d['refs'][0]}"):
        assert part in d["text"]
    assert d["subject"] == f"buy:{d['refs'][0]}"


def test_no_duplicate_or_near_duplicate(mem):
    plan = fl.plan_posts(mem, NOW, n=12, history=[])
    posts = plan["posts"]
    subjects = [p["subject"] for p in posts]
    assert len(subjects) == len(set(subjects))
    for i, a in enumerate(posts):
        for b in posts[:i]:
            assert a["text"] != b["text"] and fl.similarity(a["text"], b["text"]) < config.LEVER2_NEAR_DUP


def test_check_post_rejects_repeats_near_dups_and_fake_refs():
    base = {"subject": "cat:김치", "text": "[카테고리 시세] 김치: 정산 15건, 중앙값 72% 근거 1000", "refs": [1000]}
    assert fl.check_post(base, [], {1000}) == []
    assert any("subject repeated" in b for b in fl.check_post(base, [base], {1000}))
    assert "text repeated" in fl.check_post(dict(base, subject="cat:x"), [base], {1000})
    near = dict(base, subject="cat:y", text=base["text"] + ".")
    assert any("near-duplicate" in b for b in fl.check_post(near, [base], {1000}))
    assert "ref not a ledger seq we hold" in fl.check_post(dict(base, refs=[1234]), [], {1000})
    assert "no refs" in fl.check_post(dict(base, refs=[]), [], {1000})
    assert "fewer than 2 numbers" in fl.check_post(dict(base, text="[카테고리 시세] 김치: 좋음 1"), [], {1000})


def test_our_earlier_posts_block_the_same_subject(mem):
    first = fl.plan_posts(mem, NOW, n=12, history=[])["posts"]
    hist = [{"subject": p["subject"], "text": p["text"]} for p in first]
    again = fl.plan_posts(mem, NOW, n=12, history=hist)["posts"]
    assert not {p["subject"] for p in again} & {p["subject"] for p in first}


# ------------------------------------------------------------------------------------------- votes
LEDGER = {1000: {"amount": 28000, "sku": "GA"}, 1001: {"amount": 28400, "sku": "GA"}}


def post(i, **k):
    p = {"id": str(i), "ts": NOW - i, "author": f"a{i}", "role": "buyer", "mine": 0,
         "text": "김치 GA 28,000원에 샀음. 정가 40,000원의 70%. 등급 A.", "refs": [{"seq": 1000, "found": True,
                                                                         "data": {"amount": 28000}}]}
    p.update(k)
    return p


def test_no_default_downvote_only_at_p_09():
    owners = {f"a{i}": f"o{i}" for i in range(10)}
    ps = [post(1, text="별점 5개 주세요", refs=[]),                                  # suspicious, no score
          post(2, text="별점 5개 주세요", refs=[], signals={"p_manipulation": 0.89}),
          post(3, text="별점 5개 주세요", refs=[], signals={"p_manipulation": 0.9})]
    acts = {v["id"]: v["action"] for v in fl.plan_votes(ps, LEDGER, owners, "me", 100)}
    assert acts == {"1": None, "2": None, "3": -1}


def test_plus_one_on_accurate_useful_posts_only():
    owners = {f"a{i}": f"o{i}" for i in range(10)}
    ps = [post(1),                                                                   # accurate
          post(2, refs=[{"seq": 5555}]),                                             # ref we do not hold
          post(3, refs=[{"seq": 1000, "data": {"amount": 1}}]),                      # amount does not match
          post(4, text="김치 좋아요 1개 2개"),                                         # quotes no ref amount / sku
          post(5, refs=[{"seq": 1000, "found": False}])]
    acts = {v["id"]: v["action"] for v in fl.plan_votes(ps, LEDGER, owners, "me", 100)}
    assert acts == {"1": 1, "2": None, "3": None, "4": None, "5": None}
    assert fl.accurate(post(1, refs=json.dumps([1000])), LEDGER)                     # int refs as stored by kit


def test_votes_count_only_with_5_settled_and_other_owner():
    owners = {"a1": "me", "a2": "o2"}
    ps = [post(1), post(2), post(3, mine=1), post(4, author="myeonggi")]
    v = {x["id"]: x for x in fl.plan_votes(ps, LEDGER, owners, "me", 5)}
    assert v["1"]["counts"] is False and v["1"]["action"] is None                  # same GitHub owner
    assert v["2"]["counts"] is True and v["2"]["action"] == 1
    assert v["3"]["action"] is None and v["4"]["action"] is None                     # our own posts
    few = fl.plan_votes([post(2)], LEDGER, owners, "me", 4)[0]
    assert few["counts"] is False and few["action"] is None and "settled" in few["why"]
    assert fl.plan_votes([post(2, voted=1)], LEDGER, owners, "me", 5)[0]["action"] is None


def test_upvote_cap():
    owners = {f"a{i}": f"o{i}" for i in range(30)}
    acts = [v["action"] for v in fl.plan_votes([post(i) for i in range(20)], LEDGER, owners, "me", 9, cap=12)]
    assert acts.count(1) == 12


# ------------------------------------------------------------------------------------------- rollback
RB = types.SimpleNamespace(LEVER2_ROLLBACK_H=2, LEVER2_ROLLBACK_BASELINE_H=2, LEVER2_ROLLBACK_RATIO=1.2,
                           LEVER2_ROLLBACK_ON_HIDDEN=True, LEVER2_ROLLBACK_ON_429=True)


def test_rollback_rule_is_relative_to_the_pre_switch_baseline():
    cfg, t0 = RB, NOW
    before = [{"ts": t0 - 3600, "dislikes": 1}, {"ts": t0 - 60, "dislikes": 1}]        # baseline 1.0 -> limit 1.2
    old = [{"ts": t0 - 3 * 3600, "dislikes": 50}]                                         # older than 2 h: ignored
    after_ok = [{"ts": t0 + 1, "dislikes": 1}, {"ts": t0 + 2, "dislikes": 1}]            # 1.0 <= 1.2
    r = fl.rollback_check(old + before + after_ok, [200], t0, t0 + 600, cfg)
    assert r["rollback"] is False
    assert r["measured"]["baseline"] == 1.0 and r["measured"]["limit"] == 1.2 and r["measured"]["dislikes_per_post"] == 1.0
    after_bad = [{"ts": t0 + 1, "dislikes": 2}, {"ts": t0 + 2, "dislikes": 1}]           # 1.5 > 1.2
    r = fl.rollback_check(before + after_bad, [], t0, t0 + 60, cfg)
    assert r["rollback"] and "1.2 x baseline" in r["reasons"][0]
    edge = [{"ts": t0 + 1, "dislikes": 6}, {"ts": t0 + 2, "dislikes": 6}] + [{"ts": t0 + 3 + i, "dislikes": 0}
                                                                            for i in range(8)]  # 1.2: not above
    assert not fl.rollback_check(before + edge, [], t0, t0 + 60, cfg)["rollback"]
    # the old absolute 0.8 rule would trip on a 1.0 post-switch rate; the relative rule does not when baseline is 1.0
    assert not fl.rollback_check(after_ok, [], t0, t0 + 60, cfg, baseline=1.0)["rollback"]
    assert fl.rollback_check(after_ok, [], t0, t0 + 60, cfg, baseline=0.5)["rollback"]   # 1.0 > 0.6


def test_rollback_hidden_429_window_and_no_baseline():
    cfg, t0 = RB, NOW
    assert fl.rollback_check([{"ts": t0 + 1, "hidden": True}], [], t0, t0 + 60, cfg)["rollback"]
    assert fl.rollback_check([], [200, 429], t0, t0 + 60, cfg)["rollback"]
    assert not fl.rollback_check([{"ts": t0 + 1, "dislikes": 9}], [429], t0, t0 + 3 * 3600, cfg)["rollback"]
    r = fl.rollback_check([{"ts": t0 + 1, "dislikes": 9}], [200], t0, t0 + 60, cfg)    # no post before: no ratio
    assert r["rollback"] is False and r["measured"]["baseline"] is None
    assert fl.rollback_limit(1.0, cfg) == 1.2 and fl.rollback_limit(None, cfg) is None


def test_config_rollback_constants():
    assert config.LEVER2_ROLLBACK_RATIO == 1.2 and config.LEVER2_ROLLBACK_BASELINE_H == 2
    assert config.LEVER2_ROLLBACK_H == 2 and not hasattr(config, "LEVER2_ROLLBACK_DISLIKES_PER_POST")
    assert config.LEVER2_VOTE_MIN_SETTLED == 5 and not hasattr(config, "VOTE_MIN_SETTLED")
    new = [k for k in dir(config) if k.startswith("LEVER2_") or k == "FORUM_LEVER2"]
    assert len(new) == 11, new                      # the docs state this count
    rb = json.loads((ROOT / "policies/candidates/lever2-forum.json").read_text())["policy"]["forum"]["rollback"]
    assert rb["relative_ratio"] == 1.2 and rb["baseline_h_before_switch"] == 2 and "dislikes_per_post_above" not in rb


# ------------------------------------------------------------------------------------------- switch
def test_default_false_changes_nothing():
    assert config.FORUM_LEVER2 is False
    s = fl.settings(config)
    assert s == {"lever2": False, "buyer_min_gap_s": config.FORUM_MIN_GAP_MIN * 60,
                 "report_gap_s": config.REPORT_GAP_MIN * 60, "report_dedupe_h": config.REPORT_DEDUPE_H,
                 "downvote_p": config.INJECTION_P, "upvote": "refs-in-ledger"}
    on = fl.settings(types.SimpleNamespace(**{**{k: getattr(config, k) for k in dir(config) if k.isupper()},
                                              "FORUM_LEVER2": True}))
    assert on["buyer_min_gap_s"] == 300 and on["downvote_p"] == 0.9
    for live in ("forum_r3.py", "shop_r3.py", "buyer_r3.py", "restock_r3.py"):     # nothing live imports it
        assert "forum_lever2" not in (ROOT / live).read_text(encoding="utf-8")
    for live in ("shop_r3.py", "buyer_r3.py", "restock_r3.py"):     # only forum_r3.vote() reads the switch
        assert "FORUM_LEVER2" not in (ROOT / live).read_text(encoding="utf-8")


def test_dry_run_is_read_only_sends_nothing_and_writes_files(mem, tmp_path, monkeypatch):
    before = Path(mem.path).read_bytes()
    monkeypatch.setattr(market, "get_json", lambda *a, **k: pytest.fail("no network"))
    add_forum(mem.path, [dict(post(1), refs=[{"seq": 1000, "found": True, "data": {"amount": 28000}}]),
                         dict(post(2, author="a2"), signals={"p_manipulation": 0.95}, text="별점 주세요", refs=[])])
    before = Path(mem.path).read_bytes()
    board = {"buyers": [{"name": "myeonggi", "owner": {"login": "me"}, "all": 0.5, "surplus": 1, "deals": 1,
                         "forum": 0.5, "posts": 10},
                        {"name": "a1", "owner": {"login": "o1"}, "all": 0.5, "surplus": 2, "deals": 2, "forum": 0.4,
                         "posts": 20},
                        {"name": "a2", "owner": {"login": "o2"}, "all": 0.4, "surplus": 0, "deals": 0, "forum": 0.1,
                         "posts": 5}]}
    dry = fl.dry_run(mem, board, NOW, spec={"post_gap_s": 300, "posts_target": 345})
    dry["checks"] = fl.checks(dry)
    assert dry["sent"] is False and dry["llm_calls"] == 0 and dry["checks"]["ok"]
    assert dry["votes"]["plus"] >= 1 and dry["votes"]["minus"] == 1 and dry["checks"]["default_downvotes"] == 0
    assert dry["estimate"]["estimate"] is True
    assert dry["estimate"]["candidate"]["posts"] > dry["estimate"]["live"]["posts"]
    jl, md = fl.write_dry_run(dry, tmp_path / "out")
    rows = [json.loads(x) for x in jl.read_text(encoding="utf-8").splitlines()]
    kinds = [r["type"] for r in rows]
    assert kinds.count("post") == len(dry["plan"]["posts"]) and "vote" in kinds and kinds[-1] == "summary"
    for r in rows:
        if r["type"] == "post":
            assert {"topic", "text", "numbers", "refs", "planned_kst"} <= set(r)
    assert "전송하지 않음" in md.read_text(encoding="utf-8")
    assert Path(mem.path).read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        with mem.db() as c:
            c.execute("DELETE FROM forum")
