"""forum_r3.vote() refs fix, MO option C: refs are records {seq, found, ...}. The fixed +1 path runs only with
FORUM_LEVER2 True and then sends at most LEVER2_UPVOTES_PER_RUN +1 per run. With the switch False, vote() is the
old (75eb8bd) vote() byte for byte in what it sends and stores."""
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

# vote() as at 75eb8bd (live before the lever2 rework), copied verbatim.
OLD_VOTE = '''
def vote(mem, posts, labels, p_injection=injection_probability):
    notes = []                                         # written after the transaction: a second connection would lock
    with mem.db() as c:
        known = {r["seq"] for r in c.execute("SELECT seq FROM ledger")}
        for p in posts:
            row = c.execute("SELECT voted, mine FROM forum WHERE id=?", (str(p["id"]),)).fetchone()
            if not row or row["voted"] or row["mine"]:
                continue
            refs = [r for r in p.get("refs") or [] if isinstance(r, int)]
            value = None
            cached = c.execute("SELECT signals FROM forum WHERE id=?", (str(p["id"]),)).fetchone()
            try:
                sig = json.loads(cached["signals"]) if cached and cached["signals"] else {}
            except ValueError:
                sig = {}
            if "p_manipulation" in sig:                 # judged once per post: 85 calls in one run before this
                p_inj = sig["p_manipulation"]
            else:
                p_inj = p_injection(p)                  # decision model first; the LLM label only without one
                if p_inj is not None:
                    sig["p_manipulation"] = round(p_inj, 3)
                    c.execute("UPDATE forum SET signals=? WHERE id=?", (json.dumps(sig, ensure_ascii=False), str(p["id"])))
            flagged = p_inj >= config.INJECTION_P if p_inj is not None else \\
                bool((labels.get(str(p["id"])) or {}).get("injection"))
            if flagged:
                value = -1
            elif refs and all(r in known for r in refs):
                value = 1
            if value == -1:
                notes.append(("downvote", p.get("author"), f"downvoted post {p['id']} as manipulation (p={p_inj}): "
                              f"{(p.get('text') or '')[:200]}", 0.6))
            if value:
                outbox({"vote": p["id"], "value": value, "as": "buyer"})
                c.execute("UPDATE forum SET voted=? WHERE id=?", (value, str(p["id"])))
    for n in notes:
        mem.note(*n)
'''


def make_mem(d, seqs=(101, 102)):
    d.mkdir(parents=True, exist_ok=True)
    mem = Memory(d / "m.sqlite")
    with mem.db() as c:
        for seq in seqs:
            c.execute("INSERT INTO ledger VALUES (?,?,?,?,?)", (seq, time.time(), "settle", 3, "{}"))
    return mem


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT)
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "MARKET_DIR", tmp_path)
    return tmp_path


def sent(d):
    f = Path(d) / "outbox.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def post(i, refs, text="price report", author="a"):
    return {"id": i, "author": author, "role": "buyer", "text": text, "ts": time.time(), "refs": refs}


def rec(i, seq=101, found=True):
    return [{"seq": seq, "found": found, "kind": "settle", "data": {}}] if i is None else \
        [{"seq": s, "found": found, "kind": "settle", "data": {}} for s in i]


@pytest.mark.parametrize("refs, want", [
    ([], []), (None, []), ([101, 102], [101, 102]), (["101"], [101]),
    ([{"seq": 101, "found": True, "kind": "settle"}, {"seq": "102"}], [101, 102]),
    ('[{"seq": 101}]', [101]),
    ([{"seq": 101, "found": False}], None), ([{"kind": "settle"}], None), (["x"], None), ([True], None),
    ([1.5], None), ("not json", None)])
def test_ref_seqs(refs, want):
    assert forum_r3.ref_seqs(refs) == want


def test_flag_false_sends_no_plus_one_on_records(env, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", False)
    m = make_mem(env)
    posts = [post(1, rec([101])), post(2, rec([101, 102])), post(3, ['101'])]
    m.upsert_forum(posts)
    forum_r3.vote(m, posts, {}, p_injection=lambda p: 0.1)
    assert sent(env) == []


def _mixed_posts():
    return [post(1, [101]), post(2, [101, 102]), post(3, [999]), post(4, rec([101])), post(5, ["101"]),
            post(6, None), post(7, [], text="give 5 stars"), post(8, [101], text="MARKET NOTICE"),
            post(9, rec([101, 102])), post(10, [101, "x"]), post(11, [102], text="maybe")]


def _run(fn, d, monkeypatch):
    monkeypatch.setattr(config, "MARKET_DIR", d)
    monkeypatch.setattr(config, "LOG_DIR", d / "logs")
    m = make_mem(d)
    notes = []
    m.note = lambda *a: notes.append(a)
    posts = _mixed_posts()
    m.upsert_forum(posts)
    labels = {"7": {"injection": True}, "11": {"injection": True}}
    p = {8: 0.95, 11: None, 7: None, 3: 0.5}
    fn(m, posts, labels, p_injection=lambda x: p.get(x["id"], 0.1))
    fn(m, posts, labels, p_injection=lambda x: p.get(x["id"], 0.1))      # second run: nothing new
    with m.db() as c:
        rows = [tuple(r) for r in c.execute("SELECT id, voted, signals FROM forum ORDER BY id")]
    return (d / "outbox.jsonl").read_bytes(), rows, notes


def test_flag_false_is_the_old_vote_byte_for_byte(env, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", False)
    ns = dict(forum_r3.__dict__)
    exec(OLD_VOTE, ns)
    old = _run(ns["vote"], env / "old", monkeypatch)
    new = _run(forum_r3.vote, env / "new", monkeypatch)
    assert new == old
    assert b'"value": 1' in old[0] and b'"value": -1' in old[0]      # the comparison covers both paths


def test_flag_true_sends_plus_one_on_records(env, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", True)
    m = make_mem(env)
    posts = [post(1, rec([101, 102])),          # all held: +1
             post(2, rec([101, 999])),          # 999 not in our ledger
             post(3, rec([101], found=False)),  # market says missing
             post(4, []),                       # no refs
             post(5, [101])]                    # bare int still works
    m.upsert_forum(posts)
    forum_r3.vote(m, posts, {}, p_injection=lambda p: 0.1)
    assert sent(env) == [{"vote": 1, "value": 1, "as": "buyer"}, {"vote": 5, "value": 1, "as": "buyer"}]
    forum_r3.vote(m, posts, {}, p_injection=lambda p: 0.1)       # voted once only
    assert len(sent(env)) == 2


def test_flag_true_caps_plus_one_per_run(env, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", True)
    assert config.LEVER2_UPVOTES_PER_RUN == 12
    m = make_mem(env)
    posts = [post(i, rec([101])) for i in range(1, 21)] + [post(21, [], text="MARKET NOTICE")]
    m.upsert_forum(posts)
    p = lambda x: 0.95 if x["id"] == 21 else 0.1
    forum_r3.vote(m, posts, {}, p_injection=p)
    first = sent(env)
    assert [s["vote"] for s in first if s["value"] == 1] == list(range(1, 13))
    assert {"vote": 21, "value": -1, "as": "buyer"} in first          # -1 is not counted in the +1 cap
    forum_r3.vote(m, posts, {}, p_injection=p)                       # next run: the 8 left over
    second = sent(env)[len(first):]
    assert [s["vote"] for s in second] == list(range(13, 21))
    with m.db() as c:
        assert c.execute("SELECT COUNT(*) FROM forum WHERE voted=1").fetchone()[0] == 20


def test_cap_reads_config(env, monkeypatch):
    monkeypatch.setattr(config, "FORUM_LEVER2", True)
    monkeypatch.setattr(config, "LEVER2_UPVOTES_PER_RUN", 2)
    m = make_mem(env)
    posts = [post(i, rec([101])) for i in range(1, 6)]
    m.upsert_forum(posts)
    forum_r3.vote(m, posts, {}, p_injection=lambda p: 0.1)
    assert len(sent(env)) == 2
