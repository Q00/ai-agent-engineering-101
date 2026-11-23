"""market_db.SKU_RE fixed (person 11/11 ~21:50, option 1): SKUs named in '구합니다' posts reach the forum replies
(depts/cs.render_want, the real path) and forum_watch's counts, but not the buying plan (market_db.signals keeps
stats "wanted" empty of them, as before the fix). Temp db, faked forum file and prices; live files untouched.
    python tests/test_forum_sku.py"""
import os
import sqlite3
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy  # noqa: E402

policy()
import market_db as M  # noqa: E402
from depts import cs  # noqa: E402

NOW = time.time()
POSTS = [{"id": 1, "ts": NOW - 600, "author": "other", "role": "buyer",
          "text": "[구합니다] 김치 찾습니다 - 절임배추 (G4a998429) 입고하시면 사겠습니다"},
         {"id": 2, "ts": NOW - 600, "author": "other", "role": "buyer", "text": "구합니다 XG4a9984290 김치"},
         {"id": 3, "ts": NOW - 600, "author": "shop1", "role": "shop", "text": "G4a998429 팝니다"}]
LEAVES = {"김치", "과자"}


def test_regex_and_demand_posts():
    assert M.SKU_RE.findall("a (G4a998429), b G1f2acd72.") == ["G4a998429", "G1f2acd72"]
    assert M.SKU_RE.findall("XG4a9984290") == []
    d = M.demand_posts(POSTS, LEAVES)
    assert [x["id"] for x in d] == [1, 2] and d[0]["skus"] == ["G4a998429"] and d[1]["skus"] == []
    assert d[0]["cats"] == ["김치"]


def test_signals_keep_buying_unchanged():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE settles (seq, ts, buyer, shop_url, shop, sku, grade, claim_honest)")
    db.execute("CREATE TABLE broken (seq, ts, buyer, shop_url, shop, sku)")
    with mock.patch.object(M, "load_forum", lambda: POSTS), mock.patch.object(M, "catalog_leaves", lambda: LEAVES), \
            mock.patch.object(M, "shop_scores", lambda db: {}):
        out = M.signals(db, {})
    assert out["wanted"] == [] and out["wanted_cats"] == ["김치"]       # no forum SKU in the buying plan


def test_want_reply_names_the_sku():
    p = os.path.join(tempfile.mkdtemp(), "m.db")
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE want_replies (ts, post, cat, sku)")
    units = [{"unit": "u1", "sku": "G0000aaaa", "name": "다른 김치", "category": "식품>김치", "supplied_at": 1,
              "kind": "goods", "grade": "A", "cost": 1},
             {"unit": "u2", "sku": "G4a998429", "name": "절임배추 20kg", "category": "식품>김치", "supplied_at": 2,
              "kind": "goods", "grade": "A", "cost": 1}]
    with mock.patch.object(M, "load_forum", lambda: POSTS), mock.patch.object(M, "catalog_leaves", lambda: LEAVES), \
            mock.patch.object(cs, "_live", lambda inv: units), mock.patch.object(cs, "_price", lambda sku: 30000), \
            mock.patch.object(cs, "floor", lambda u, avg, case, now=None: (29000, "x")), \
            mock.patch.object(cs.strategy, "shop_case", lambda inv, now=None: "normal"):
        r = cs.render_want({"id": 1}, {"units": units}, db)
    assert r and "G4a998429" in r["post"] and r["thread"] == 1, r          # the named SKU first (was: by expiry)
    assert db.execute("SELECT sku FROM want_replies").fetchone()[0] == "G4a998429"


if __name__ == "__main__":
    test_regex_and_demand_posts()
    test_signals_keep_buying_unchanged()
    test_want_reply_names_the_sku()
    print("ok")
