"""Review-discount pilot (review-discount-v1). No network, no model calls, no real key.

Contract (initial agent-chosen assumptions, not tuned values):
- eligibility = a public forum post whose author (role buyer, market-stated owner = board owner of that buyer name,
  owner != this shop's owner) references the seq of a REAL round-3 settle row, re-read by us from the market ledger,
  whose shop is one of this agent's registered URLs and whose buyer is that author. Post text/opinion is never read.
- the coupon is a PUBLIC BEARER token ("REVIEW-COUPON <post id>"): A2A carries no buyer identity, so whoever sends
  it redeems it; limited to one redemption per post, per original settlement and per author.
- discount <= 5% of the normal bounded opening quote, price stays >= cost + 1; cumulative budget 10,000 credits
  reserved at quote time, committed only when an exact public settle row (own shop URL + unit + sku + amount) shows.
- a reservation is released only while unsigned; a signed one waits for settlement (late payment) and never expires.
REAL_SETTLE is copied verbatim from logs/review-discount-pilot/ledger-tail.json (GET 2026-11-07 16:31 KST).
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import review_discount as rd

REAL_SETTLE = {"seq": 3605905, "ts": 1794036678.552, "kind": "settle", "round": 3, "data": {
    "order": "o-69fea8bbbca5", "buyer": "psyoung82", "shop": "https://prayer-comparison-onto-votes.trycloudflare.com",
    "item": "G2cb8041b", "amount": 206447, "checkout_hash": "jm_R8NjVtU63c5a1Ys6KqIx_Z13_K1Gzz3VmaZNeLXU",
    "unit": "u-76117f1cffa4", "sku": "G2cb8041b", "name": "T800MQ", "grade": "A", "expires_at": None,
    "claim": "A", "claim_honest": True, "warranty_hours": 6.0},
    "prev": "bc95dfac2a205dd00304c45d7279457959d791238f1335f2a6fc5623870a9a40",
    "hash": "85c33ade763dfc7c8e63bd3416ab5c52f0577358a2dbf30608baea96e110f3fd"}
OWN = ["https://prayer-comparison-onto-votes.trycloudflare.com"]     # fixture: treat that row's shop as ours
OWN_OWNER = "kimchyoungman"
OWNERS = {"psyoung82": "psyoung82"}                                  # board buyers[].name -> owner.login
# forum.json row shape (kit .market/forum.json, 2026-11-07): refs resolved by the market as {seq, found, kind, data}
POST = {"id": 40001, "ts": REAL_SETTLE["ts"] + 600, "round": 3, "author": "psyoung82", "role": "buyer",
        "owner": "psyoung82", "thread": None, "text": "Terrible shop, slow and overpriced.",
        "refs": [{"seq": 3605905, "found": True, "kind": "settle", "data": REAL_SETTLE["data"]}],
        "likes": 0, "dislikes": 3, "score": -3}


def verify(post=POST, rows=None, own=OWN, owners=OWNERS, own_owner=OWN_OWNER):
    rows = {REAL_SETTLE["seq"]: REAL_SETTLE} if rows is None else rows
    return rd.verify_post(post, rows, own, owners, own_owner)


class EligibilityTests(unittest.TestCase):
    def test_negative_post_bound_to_own_settle_by_its_buyer_is_eligible(self):
        got, reason = verify()
        self.assertEqual(reason, "eligible")
        self.assertEqual((got["post"], got["author"], got["settle_seq"], got["unit"], got["sku"], got["amount"]),
                         (40001, "psyoung82", 3605905, "u-76117f1cffa4", "G2cb8041b", 206447))

    def test_owner_ref_url_binding_rejects_every_unbound_post(self):
        def post(**kw):
            return dict(copy.deepcopy(POST), **kw)

        def row(**data):
            r = copy.deepcopy(REAL_SETTLE)
            r["data"].update(data)
            return {r["seq"]: r}
        other_shop = row(shop="https://someone-else.trycloudflare.com")
        cases = {
            "author_not_settle_buyer": verify(post(author="buyer-03", owner="x"), owners={"buyer-03": "x"}),
            "author_role_shop": verify(post(role="shop")),
            "owner_not_registry_owner": verify(post(owner="mallory")),
            "author_unknown_to_registry": verify(owners={}),
            "own_owner_trade": verify(post(owner=OWN_OWNER), owners={"psyoung82": OWN_OWNER}),
            "settle_at_other_shop": verify(rows=other_shop),
            "ref_found_only_in_post_copy": verify(rows={}),
            "ref_not_settle": verify(rows={3605905: dict(REAL_SETTLE, kind="order")}),
            "ref_not_round3": verify(rows={3605905: dict(REAL_SETTLE, round=2)}),
            "post_before_settle": verify(post(ts=REAL_SETTLE["ts"] - 1)),
            "no_unit": verify(rows=row(unit=None)),
            "no_refs": verify(post(refs=[])),
            "refs_injection": verify(post(refs="3605905; DROP TABLE")),
            "bool_seq": verify(post(refs=[True])),
            "id_not_int": verify(post(id="40001")),
            "not_a_dict": rd.verify_post(["x"], {}, OWN, OWNERS, OWN_OWNER),
            "no_own_urls": verify(own=[]),
        }
        for name, (got, reason) in cases.items():
            with self.subTest(name):
                self.assertIsNone(got)
                self.assertNotEqual(reason, "eligible")


T0 = 1794040000          # fixture activation epoch (the real one is set by the parent at activation)
UNIT = {"unit": "u-next-1", "sku": "Gnext", "cost": 9000, "list_price": 20000}


class StateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.config()
        self.evidence(POST)

    def config(self, **kw):
        cfg = dict(pilot=rd.PILOT, activated_at=T0, expires_at=T0 + 86400, max_pct=5, budget=10000,
                   own_owner=OWN_OWNER)
        cfg.update(kw)
        (self.state / rd.CONFIG).write_text(json.dumps(cfg), encoding="utf-8")

    def evidence(self, *posts):
        eligible = {}
        for i, p in enumerate(posts):
            r = copy.deepcopy(REAL_SETTLE)
            r["seq"] += i
            r["data"]["buyer"] = p["author"]
            rows = {r["seq"]: r}
            p = dict(p, refs=[REAL_SETTLE["seq"] + i])
            got, reason = rd.verify_post(p, rows, OWN, OWNERS, OWN_OWNER)
            self.assertEqual(reason, "eligible")
            eligible[str(p["id"])] = got
        (self.state / rd.EVIDENCE).write_text(json.dumps({"version": rd.EVIDENCE_VERSION, "eligible": eligible}),
                                              encoding="utf-8")

    def reserve(self, post=40001, task="t1", unit=UNIT, quote=18000, now=T0 + 10):
        return rd.reserve(self.state, post, task, unit, quote, now)

    def ledger(self):
        return json.loads((self.state / rd.LEDGER).read_text(encoding="utf-8"))


class ReserveTests(StateCase):
    def test_reserve_caps_discount_at_5pct_floors_above_cost_and_holds_budget(self):
        res, reason = self.reserve()
        self.assertEqual(reason, "reserved")
        self.assertEqual((res["normal_quote"], res["discount"], res["discounted_quote"], res["status"]),
                         (18000, 900, 17100, "reserved"))
        self.assertEqual((res["post"], res["settle_seq"], res["author"], res["task"]), (40001, 3605905, "psyoung82", "t1"))
        self.assertEqual(rd.budget_view(self.ledger(), 10000), {"reserved": 900, "committed": 0, "available": 9100})

    def test_floor_keeps_positive_margin_and_no_margin_means_regular_price(self):
        res, reason = self.reserve(quote=9050)
        self.assertEqual((res["discount"], res["discounted_quote"]), (49, 9001))
        self.setUp()
        res, reason = self.reserve(quote=9001)
        self.assertIsNone(res)
        self.assertEqual(reason, "no_margin")


def settle_row(seq, unit="u-next-1", sku="Gnext", amount=17100, shop=OWN[0], buyer="anyone", kind="settle"):
    r = copy.deepcopy(REAL_SETTLE)
    r.update(seq=seq, kind=kind, ts=T0 + 100)
    r["data"].update(unit=unit, sku=sku, item=sku, amount=amount, shop=shop, buyer=buyer,
                     checkout_hash=f"hash-{seq}")
    return r


class LifecycleTests(StateCase):
    def status(self, task="t1"):
        return next(r for r in self.ledger()["reservations"] if r["task"] == task)

    def test_quote_and_signature_are_not_settlement_and_only_exact_settle_commits_once(self):
        res, _ = self.reserve()
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20), "signed")
        self.assertEqual(self.status()["status"], "signed")
        # unsigned-only release: a signed receipt can still be paid late, so neither cancel nor timeout frees it
        self.assertEqual(rd.release(self.state, "t1", "cancel", T0 + 30), "signed_kept")
        self.assertEqual(rd.release(self.state, "t1", "timeout", T0 + 10 ** 7), "signed_kept")
        self.assertEqual(self.status()["status"], "signed")
        near_misses = [settle_row(1, amount=17101), settle_row(2, unit="u-other"), settle_row(3, sku="Gx"),
                       settle_row(4, shop="https://someone-else.trycloudflare.com"), settle_row(5, kind="order")]
        self.assertEqual(rd.reconcile(self.state, near_misses, OWN, T0 + 200), [])
        self.assertEqual(self.status()["status"], "signed")
        got = rd.reconcile(self.state, near_misses + [settle_row(9, buyer="psyoung82")], OWN, T0 + 300)
        self.assertEqual([g["task"] for g in got], ["t1"])
        r = self.status()
        self.assertEqual((r["status"], r["redeem_seq"], r["redeem_checkout_hash"], r["redeemed_by"],
                          r["redeemed_by_author"]), ("committed", 9, "hash-9", "psyoung82", True))
        self.assertEqual(rd.reconcile(self.state, [settle_row(9)], OWN, T0 + 400), [])     # once only
        self.assertEqual(rd.budget_view(self.ledger(), 10000), {"reserved": 0, "committed": 900, "available": 9100})

    def test_unsigned_cancel_releases_budget_and_post_but_never_marks_redeemed(self):
        self.reserve()
        self.assertEqual(rd.release(self.state, "t1", "cancel", T0 + 30), "released")
        self.assertEqual(self.status()["status"], "released")
        self.assertNotIn("redeem_seq", self.status())
        self.assertEqual(rd.budget_view(self.ledger(), 10000)["available"], 10000)
        res, reason = self.reserve(task="t2")
        self.assertEqual(reason, "reserved")


class GuardTests(StateCase):
    def test_one_per_post_per_original_settlement_per_author(self):
        other_author = dict(POST, id=40002, author="buyer-x", owner="ox")
        OWNERS["buyer-x"] = "ox"
        self.addCleanup(OWNERS.pop, "buyer-x")
        same_author = dict(POST, id=40003)
        rows = {3605905: REAL_SETTLE}
        e1, _ = rd.verify_post(POST, rows, OWN, OWNERS, OWN_OWNER)
        e3, _ = rd.verify_post(dict(same_author, refs=[3605905]), rows, OWN, OWNERS, OWN_OWNER)
        e2 = dict(e1, post=40002, author="buyer-x", owner="ox")          # second post citing the SAME settlement
        ev = {"version": rd.EVIDENCE_VERSION, "eligible": {"40001": e1, "40002": e2, "40003": e3}}
        (self.state / rd.EVIDENCE).write_text(json.dumps(ev), encoding="utf-8")
        self.assertEqual(self.reserve(task="a")[1], "reserved")
        self.assertEqual(self.reserve(task="b")[1], "already_used")             # same post
        self.assertEqual(self.reserve(post=40002, task="c")[1], "already_used")  # same original settlement
        self.assertEqual(self.reserve(post=40003, task="d")[1], "already_used")  # same author, other post
        self.assertEqual(self.reserve(post=40001, task="a")[1], "task_already_reserved")

    def test_concurrent_tasks_cannot_both_reserve_one_post(self):
        import threading
        out, go = [], threading.Barrier(8)

        def worker(i):
            go.wait()
            out.append(self.reserve(task=f"c{i}")[1])
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(out.count("reserved"), 1, out)
        self.assertEqual(sum(r["status"] == "reserved" for r in self.ledger()["reservations"]), 1)

    def test_budget_is_reserved_at_quote_and_exhaustion_means_regular_price(self):
        self.config(budget=1000)
        posts = [dict(POST, id=40010 + i, author=f"b{i}", owner=f"o{i}") for i in range(2)]
        for i in range(2):
            OWNERS[f"b{i}"] = f"o{i}"
            self.addCleanup(OWNERS.pop, f"b{i}")
        self.evidence(*[dict(p) for p in posts])
        ev = json.loads((self.state / rd.EVIDENCE).read_text(encoding="utf-8"))
        self.assertEqual(len(ev["eligible"]), 2)
        self.assertEqual(self.reserve(post=40010, task="x")[1], "reserved")          # 900 of 1000
        self.assertEqual(self.reserve(post=40011, task="y")[1], "budget")            # 900 more would exceed

    def test_expiry_and_inactive_window_and_config_ceilings(self):
        self.assertEqual(self.reserve(now=T0 - 1)[1], "inactive")
        self.assertEqual(self.reserve(now=T0 + 86400)[1], "inactive")
        for bad in ({"max_pct": 6}, {"budget": 10001}, {"expires_at": T0 + 86401}, {"pilot": "risk-exception-v1"},
                    {"max_pct": float("nan")}, {"own_owner": ""}):
            with self.subTest(bad):
                self.config(**bad)
                self.assertEqual(self.reserve()[1], "no_pilot")
        (self.state / rd.CONFIG).unlink()
        self.assertEqual(self.reserve()[1], "no_pilot")

    def test_corrupt_ledger_or_evidence_fails_closed(self):
        (self.state / rd.LEDGER).write_text("{not json", encoding="utf-8")
        self.assertEqual(self.reserve()[1], "ledger_corrupt")
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0), "ledger_corrupt")
        self.assertEqual(rd.reconcile(self.state, [settle_row(9)], OWN, T0), [])
        self.assertEqual((self.state / rd.LEDGER).read_text(encoding="utf-8"), "{not json")     # untouched
        (self.state / rd.LEDGER).unlink()
        (self.state / rd.EVIDENCE).write_text('{"version": 1, "eligible": []}', encoding="utf-8")
        self.assertEqual(self.reserve()[1], "evidence_unavailable")

    def test_coupon_parse_is_strict(self):
        self.assertEqual(rd.parse_coupon("I want a desk. REVIEW-COUPON 40001"), 40001)
        self.assertEqual(rd.parse_coupon("review-coupon: #40001 please"), 40001)
        for text in ("I want a desk", "coupon 40001", "REVIEW-COUPON 40001 REVIEW-COUPON 40002",
                     "REVIEW-COUPON 4000100000000000", "REVIEW-COUPON -5", "REVIEW-COUPON 40001x", None, 40001,
                     "REVIEW-COUPON 0"):
            with self.subTest(text):
                self.assertIsNone(rd.parse_coupon(text))


BOARD = {"round": 3, "shops": [{"name": "fair-furniture", "owner": {"login": OWN_OWNER}, "card": {"url": OWN[0]}}],
         "buyers": [{"name": "psyoung82", "owner": {"login": "psyoung82"}}]}


class FakeMarket:
    """Stands in for rd._get: records every path; answers the fixed-origin reads the refresher may make."""

    def __init__(self, ledger=None, fail=None):
        self.paths, self.ledger, self.fail = [], {REAL_SETTLE["seq"]: REAL_SETTLE} if ledger is None else ledger, fail

    def __call__(self, path):
        self.paths.append(path)
        if self.fail:
            raise self.fail
        if path == "/api/board":
            body = BOARD
        else:
            after = int(path.split("after=")[1])
            body = [r for s, r in sorted(self.ledger.items()) if s > after][:500]
        raw = json.dumps(body).encode()
        return body, rd.hashlib.sha256(raw).hexdigest(), len(raw)


class RefreshTests(StateCase):
    def setUp(self):
        super().setUp()
        (self.state / rd.EVIDENCE).unlink()
        noise = [dict(POST, id=1, role="shop"), dict(POST, id=2, refs=[]), "junk",
                 dict(POST, id=3, refs=[{"seq": 7, "found": True, "kind": "settle",
                                          "data": {"shop": "https://someone-else.trycloudflare.com"}}])]
        (self.state / "forum.json").write_text(json.dumps(noise + [POST]), encoding="utf-8")

    def refresh(self, market, now=T0 + 10):
        return rd.refresh_evidence(self.state, now, own_urls=OWN, fetch=market)

    def test_refresh_verifies_candidates_against_market_reads_with_provenance_and_rate_limit(self):
        m = FakeMarket()
        ev = self.refresh(m)
        self.assertEqual(sorted(ev["eligible"]), ["40001"])
        self.assertEqual(m.paths, ["/api/board", "/api/ledger?after=3605904"])   # only the own-shop candidate
        self.assertEqual(ev["counts"]["posts_scanned"], 5)
        self.assertEqual(ev["counts"]["candidates"], 1)
        self.assertEqual({s["path"] for s in ev["sources"]}, {"/api/board", "/api/ledger?after=3605904"})
        self.assertTrue(all(len(s["sha256"]) == 64 and s["ok"] for s in ev["sources"]))
        self.assertEqual(len(ev["forum_sha256"]), 64)
        self.assertEqual(rd.load_evidence(self.state)["eligible"]["40001"]["settle_seq"], 3605905)
        m2 = FakeMarket()
        self.refresh(m2, now=T0 + 30)                     # within MIN_INTERVAL: no network at all
        self.assertEqual(m2.paths, [])

    def test_missing_ref_or_fetch_failure_means_no_new_eligibility_and_keeps_verified(self):
        ev = self.refresh(FakeMarket(ledger={}))
        self.assertEqual(ev["eligible"], {})
        self.assertEqual(ev["rejected"]["40001"], "ref_unverified")
        ev = self.refresh(FakeMarket(), now=T0 + 100)
        self.assertIn("40001", ev["eligible"])
        forum = json.loads((self.state / "forum.json").read_text(encoding="utf-8")) + [dict(POST, id=40005)]
        (self.state / "forum.json").write_text(json.dumps(forum), encoding="utf-8")
        ev = self.refresh(FakeMarket(fail=TimeoutError("slow")), now=T0 + 200)
        self.assertEqual(sorted(ev["eligible"]), ["40001"])   # verified against an immutable ledger row: kept
        self.assertFalse(ev["sources"][-1]["ok"])
        self.assertEqual(ev["sources"][-1]["error"], "TimeoutError")

    def test_no_pilot_config_means_no_network(self):
        (self.state / rd.CONFIG).unlink()
        m = FakeMarket()
        self.assertIsNone(self.refresh(m))
        self.assertEqual(m.paths, [])

    def test_get_is_fixed_origin_allowlisted_and_byte_bounded(self):
        for bad in ("https://evil.example/api/board", "/api/ledger?after=1&x=2", "/api/forum", "/api/ledger?after=a",
                    "//evil.example/api/board"):
            with self.subTest(bad), self.assertRaises(ValueError):
                rd._get(bad)
        import io
        from unittest.mock import patch
        class Resp(io.BytesIO):          # _get opens through rd._OPENER (redirects refused) and checks geturl()
            def geturl(self):
                return "https://market.ourolabs.ai/api/board"
        big = Resp(b"[" + b" " * (rd.MAX_BYTES + 10) + b"]")
        with patch.object(rd._OPENER, "open", return_value=big) as op:
            with self.assertRaises(ValueError):
                rd._get("/api/board")
        self.assertEqual(op.call_args.kwargs["timeout"], rd.TIMEOUT)
        self.assertTrue(op.call_args.args[0].full_url.startswith("https://market.ourolabs.ai/api/board"))


class TickTests(StateCase):
    def test_tick_reconciles_from_public_settlement_cache_without_new_reads(self):
        self.reserve()
        rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20)
        cache = {"rows": [settle_row(77, buyer="someone-else")], "own_urls": OWN, "fetched_at": T0 + 50}
        (self.state / "public-settlements.json").write_text(json.dumps(cache), encoding="utf-8")
        m = FakeMarket()
        out = rd.tick(self.state, T0 + 10 ** 6, own_urls=OWN, fetch=m)     # long after expiry: reconcile still runs
        self.assertEqual(out["committed"], 1)
        r = self.ledger()["reservations"][0]
        self.assertEqual((r["status"], r["redeem_seq"], r["redeemed_by_author"]), ("committed", 77, False))

    def test_tick_without_config_does_nothing(self):
        (self.state / rd.CONFIG).unlink()
        m = FakeMarket()
        self.assertEqual(rd.tick(self.state, T0, own_urls=OWN, fetch=m), {"configured": False})
        self.assertEqual(m.paths, [])
        self.assertFalse((self.state / rd.LEDGER).exists())


class CardTests(StateCase):
    def test_card_text_only_while_active_neutral_and_discloses_bearer(self):
        text = rd.card_text(self.state, T0 + 10)
        for must in ("검증 가능한 실제 거래후기 게시글", "평가 방향 무관", "5%", "10,000", "REVIEW-COUPON", "bearer"):
            self.assertIn(must, text)
        for never in ("교수", "approved", "승인", "별점 5", "좋은 후기"):
            self.assertNotIn(never, text)
        self.assertEqual(rd.card_text(self.state, T0 + 86400), "")
        (self.state / rd.CONFIG).unlink()
        self.assertEqual(rd.card_text(self.state, T0 + 10), "")


class GateTests(unittest.TestCase):
    def test_restock_gate_binds_review_discount_source(self):
        import hashlib
        import restock_r3
        self.assertEqual(Path(restock_r3.__file__).resolve().parent, Path(__file__).resolve().parent)   # staged copy
        self.assertIn("review_discount.py", restock_r3.SOURCES)
        self.assertIn("seller_r3.py", restock_r3.SOURCES)
        src = Path(restock_r3.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            h = {n: hashlib.sha256((src / n).read_bytes()).hexdigest() for n in restock_r3.SOURCES}
            gate = {"approved": True, "policy_version": restock_r3.POLICY, "source_sha256": h}
            Path(d, "seller-v2-approved.json").write_text(json.dumps(gate), encoding="utf-8")
            self.assertTrue(restock_r3.approved(d))
            gate["source_sha256"]["review_discount.py"] = "0" * 64
            Path(d, "seller-v2-approved.json").write_text(json.dumps(gate), encoding="utf-8")
            self.assertFalse(restock_r3.approved(d))


if __name__ == "__main__":
    unittest.main()
