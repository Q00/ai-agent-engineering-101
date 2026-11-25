import json
import os
import tempfile
import unittest

import forum
import rival_vote as rv

BOARD = {"players": [
    {"player": "buzllightyear", "total": 1.448, "buyer": {"name": "buzllightyear"}},
    {"player": "MJforge", "total": 1.435, "buyer": {"name": "mjforge"}},
    {"player": "Nocha12", "total": 1.435, "buyer": {"name": "nocha12"}},
    {"player": "olstjr", "total": 1.377, "buyer": {"name": "olstjr"}},
]}
POSTS = [{"id": i, "author": "mjforge", "role": "buyer"} for i in (10, 11, 12, 13)] + \
        [{"id": 20, "author": "olstjr", "role": "buyer"}]


def sent(post, value=1, fee=0):
    return {"sent": {"vote": post, "value": value, "as": "buyer"}, "status": 200,
            "answer": json.dumps({"ok": True, "fee": fee, "next_dislike_fee": 0})}


class RivalVote(unittest.TestCase):
    def test_rivals_only_within_band(self):
        # 11/06 23:17 thisjonghoon was 0.011 above us: two or three stances plus our own Saved growth close that, so the
        # default band reaches a player 0.013 above (buzllightyear here) and still leaves olstjr 0.058 below alone.
        self.assertEqual(rv.rivals(BOARD), ["mjforge", "buzllightyear"])
        self.assertEqual(rv.rivals(BOARD, band=0.006), ["mjforge"])

    def test_band_edges(self):
        board = {"players": [
            {"player": "Nocha12", "total": 1.435, "buyer": {"name": "nocha12"}},
            {"player": "edge", "total": 1.455, "buyer": {"name": "edge"}},      # exactly 0.02 above: in
            {"player": "out", "total": 1.456, "buyer": {"name": "out"}},        # 0.021 above: out
            {"player": "below", "total": 1.42, "buyer": {"name": "below"}},     # 0.015 below: in
        ]}
        self.assertEqual(rv.rivals(board), ["below", "edge"])

    def test_flips_my_likes_with_enough_dislikes(self):
        authors = {"1": "mjforge", "2": "mjforge"}       # two posts I liked earlier
        lines, _ = rv.plan(["mjforge"], POSTS, authors, [sent(1), sent(2)], {}, 0, 1000)
        self.assertEqual([x["vote"] for x in lines], [13, 12, 11])
        self.assertTrue(all(x["value"] == -1 for x in lines))

    def test_never_pays(self):
        lines, why = rv.plan(["mjforge"], POSTS, {}, [], {}, 20000, 1000)
        self.assertEqual(lines, [])
        lines, _ = rv.plan(["mjforge"], POSTS, {}, [], {}, None, 1000)
        self.assertEqual(lines, [])
        lines, why = rv.plan(["mjforge"], POSTS, {}, [sent(99, -1, fee=20000)], {}, 0, 1000)
        self.assertEqual(lines, [])
        self.assertIn("charged", why)

    def test_hourly_and_total_caps(self):
        lines, _ = rv.plan(["mjforge"], POSTS, {}, [], {"opened": [900, 950, 990]}, 0, 1000)
        self.assertEqual(lines, [])
        lines, _ = rv.plan(["mjforge"], POSTS, {}, [], {"opened": [900 - 3600]}, 0, 1000)
        self.assertEqual(len(lines), 1)
        full = {"disliked": {f"b{i}": 0 for i in range(rv.MAX_STANCES)}}
        self.assertEqual(rv.plan(["mjforge"], POSTS, {}, [], full, 0, 1000)[0], [])
        # the cap counts stances opened in the last day: old ones stay (nothing is undone) but do not block a new rival
        day = 86400
        old = {"disliked": {f"b{i}": 0 for i in range(rv.MAX_STANCES)}}
        self.assertEqual(len(rv.plan(["mjforge"], POSTS, {}, [], old, 0, day + 1000)[0]), 1)

    def test_waits_without_enough_posts(self):
        authors = {str(i): "mjforge" for i in range(1, 6)}
        lines, _ = rv.plan(["mjforge"], POSTS, authors, [sent(i) for i in range(1, 6)], {}, 0, 1000)
        self.assertEqual(lines, [])

    def test_maybe_run_writes_once_and_forum_stops_liking(self):
        with tempfile.TemporaryDirectory() as md:
            json.dump(POSTS, open(os.path.join(md, "forum.json"), "w"))
            json.dump({"next_dislike_fee": 0}, open(os.path.join(md, "inventory.json"), "w"))
            lines = rv.maybe_run(md, fetch=lambda: BOARD, now=10000)
            self.assertEqual([x["vote"] for x in lines], [13])
            self.assertIsNone(rv.maybe_run(md, fetch=lambda: BOARD, now=10060))      # not due
            self.assertIsNone(rv.maybe_run(md, fetch=lambda: BOARD, now=10000 + 700))  # already disliked
            with open(os.path.join(md, "outbox.jsonl")) as f:
                out = [json.loads(x) for x in f]
            self.assertEqual(out, [{"vote": 13, "value": -1, "as": "buyer"}])
            self.assertEqual(rv.avoid(md), {"mjforge"})
            cfg = {"max_chars": 600, "max_votes": 10}
            final = {"post": "", "refs": [], "thread": "", "votes": [{"post": "10", "value": 1}, {"post": "20", "value": 1}]}
            got, _ = forum.clean(final, POSTS, set(), set(), cfg, avoid=rv.avoid(md))
            self.assertEqual(got, [{"vote": 20, "value": 1, "as": "buyer"}])

    def test_a_like_waiting_in_the_outbox_counts(self):
        lines, _ = rv.plan(["mjforge"], POSTS, {"10": "mjforge"}, [], {}, 0, 1000, queued=[{"vote": 10, "value": 1, "as": "buyer"}])
        self.assertEqual([x["vote"] for x in lines], [13, 12])

    def test_already_disliked_sends_nothing(self):
        state = {}
        lines, _ = rv.plan(["mjforge"], POSTS, {"1": "mjforge"}, [sent(1, -1)], state, 0, 1000)
        self.assertEqual(lines, [])
        self.assertIn("mjforge", state["disliked"])

    def test_vote_room_per_hour(self):
        authors = {"1": "mjforge", "2": "mjforge"}
        lines, _ = rv.plan(["mjforge"], POSTS, authors, [sent(1), sent(2)], {"votes": [[990, 1]]}, 0, 1000)
        self.assertEqual(lines, [])        # 3 votes needed, 2 left this hour

    def test_refused_dislike_is_released(self):
        state = {"disliked": {"mjforge": 1}}
        refused = {"sent": {"vote": 13, "value": -1, "as": "buyer"}, "status": 402, "answer": "{}"}
        rv.recheck(state, {"13": "mjforge"}, [refused], [])
        self.assertEqual(state["disliked"], {})
        state = {"disliked": {"mjforge": 1}}
        rv.recheck(state, {"13": "mjforge"}, [sent(13, -1)], [])
        self.assertIn("mjforge", state["disliked"])

    def test_avoid_survives_a_bad_state_file(self):
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, rv.STATE), "w") as f:
                json.dump([1, 2], f)
            self.assertEqual(rv.avoid(md), set())
            self.assertIsNone(rv.maybe_run(md, fetch=lambda: {"players": []}, now=10000))

    def test_posts_seen_in_earlier_runs_count(self):
        state = {}
        rv.remember_authors(state, [{"id": 5, "author": "mjforge", "role": "buyer"},
                                    {"id": 6, "author": "mjforge", "role": "shop"}])
        posts = rv.seen_posts(state, [{"id": 7, "author": "mjforge", "role": "buyer"}])
        self.assertEqual(sorted(p["id"] for p in posts if p["role"] == "buyer"), [5, 7])
        authors = {"1": "mjforge"}
        lines, _ = rv.plan(["mjforge"], posts, authors, [sent(1)], {}, 0, 1000)
        self.assertEqual([x["vote"] for x in lines], [7, 5])

    def test_a_post_i_voted_on_keeps_its_author_past_the_trim(self):
        # 11/07 05:30: state kept the newest 5,000 post authors, so 98 of my 158 counted votes had lost their author; my
        # old likes on thisjonghoon, seochanit, 25620027 and shaun0927-labbuyer dropped out of the net, which read -1
        # (a stance held) while the market's net was still +1..+3: those dislikes did nothing.
        state = {}
        rv.remember_authors(state, [{"id": 1, "author": "mjforge", "role": "buyer"}])
        rv.remember_authors(state, [{"id": i, "author": "x", "role": "buyer"} for i in range(2, 9)], keep=3, voted={1})
        self.assertEqual(state["authors"].get("1"), "mjforge")
        self.assertEqual(state["buyer_posts"].get("1"), "mjforge")
        self.assertEqual(sorted(k for k in state["authors"] if k != "1"), ["6", "7", "8"])

    def test_maybe_run_keeps_voted_authors_and_releases_a_stance_that_never_held(self):
        with tempfile.TemporaryDirectory() as md:
            json.dump(POSTS, open(os.path.join(md, "forum.json"), "w"))
            json.dump({"next_dislike_fee": 0}, open(os.path.join(md, "inventory.json"), "w"))
            # liked mjforge's posts 1 and 2 long ago, then disliked 13: the market's net is +1, no stance
            with open(os.path.join(md, "outbox.sent.jsonl"), "w") as f:
                for x in (sent(1), sent(2), [], sent(13, -1)):      # a stray non-object line is skipped
                    f.write(json.dumps(x) + "\n")
            many = {str(i): "x" for i in range(100, 100 + 5000)}
            json.dump({"authors": dict(many, **{"1": "mjforge", "2": "mjforge", "13": "mjforge"}),
                       "buyer_posts": dict(many), "disliked": {"mjforge": 9000}},
                      open(os.path.join(md, rv.STATE), "w"))
            lines = rv.maybe_run(md, fetch=lambda: BOARD, now=10000)
            state = json.load(open(os.path.join(md, rv.STATE)))
            self.assertEqual([state["authors"].get(k) for k in ("1", "2", "13")], ["mjforge"] * 3)
            self.assertEqual([x["vote"] for x in lines], [12, 11])      # net +1: two more -1 make the stance

    def test_board_failure_is_quiet(self):
        with tempfile.TemporaryDirectory() as md:
            def boom():
                raise ValueError("bad json")
            self.assertIsNone(rv.maybe_run(md, fetch=boom, now=10000))


if __name__ == "__main__":
    unittest.main()
