"""The buyer post goes out before the slow classify/vote part of a forum run (2026-11-07: posts sat 5.5 min apart)."""
import forum_r3


def test_post_before_classify_and_vote(monkeypatch, tmp_path):
    order = []

    class Mem:
        def upsert_forum(self, posts):
            return [{"id": 1, "author": "someone", "text": "x"}]

        def get(self, k, default=None):
            return 10 ** 12 if k == "shop_post_at" else default

    monkeypatch.setattr(forum_r3, "Memory", Mem)
    monkeypatch.setattr(forum_r3.market, "read_json", lambda *a, **k: [])
    monkeypatch.setattr(forum_r3, "proactive_buyer_post", lambda mem, now: order.append("proactive") or {"decision": "wait"})
    monkeypatch.setattr(forum_r3, "scheduled_report", lambda mem, now: order.append("report") or {"decision": "post"})
    monkeypatch.setattr(forum_r3, "classify", lambda mem, new: order.append("classify") or {})
    monkeypatch.setattr(forum_r3, "vote", lambda mem, new, labels: order.append("vote"))
    monkeypatch.setattr(forum_r3.config, "LOG_DIR", tmp_path)
    forum_r3.main([])
    assert order == ["proactive", "report", "classify", "vote"]
