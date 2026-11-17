"""Parent review corrections (parent-review-pending.txt 16:40 KST, additional-review.txt) for review-discount-v1.
One class per correction, written RED first. No network except a 127.0.0.1 HTTP server; no model calls, no real key."""
import copy
import json
import unittest
from pathlib import Path

import review_discount as rd
from test_review_discount import OWN, OWNERS, POST, REAL_SETTLE, T0, StateCase, settle_row


class C1OwnerOnceTests(StateCase):
    def test_same_github_owner_with_two_buyer_accounts_redeems_once(self):
        OWNERS["psyoung82-alt"] = "psyoung82"                     # second buyer account, same GitHub owner
        self.addCleanup(OWNERS.pop, "psyoung82-alt")
        self.evidence(POST, dict(POST, id=40002, author="psyoung82-alt", owner="psyoung82"))
        ev = rd.load_evidence(self.state)["eligible"]
        self.assertNotEqual(ev["40001"]["settle_seq"], ev["40002"]["settle_seq"])   # different post, settle, author
        self.assertEqual(self.reserve(post=40001, task="a")[1], "reserved")
        self.assertEqual(self.reserve(post=40002, task="b")[1], "already_used")

    def test_card_discloses_the_owner_limit(self):
        self.assertIn("글·원거래·작성자·GitHub 소유자당 1회", rd.card_text(self.state, T0 + 10))


class _Server:
    """A real HTTP server on 127.0.0.1 in a thread; `handler(path)` -> (status, headers, body). Records request paths."""

    def __init__(self, handler):
        import http.server
        import threading
        hits, outer = [], self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                status, headers, body = handler(self.path)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.hits = hits
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class C2NoRedirectTests(unittest.TestCase):
    """Production BASE stays the fixed https origin; only this test points BASE at a local server (patch, restored)."""

    def serve(self, handler):
        s = _Server(handler)
        self.addCleanup(s.close)
        return s

    def get_via(self, origin, path="/api/board"):
        from unittest.mock import patch
        with patch.object(rd, "BASE", origin.url):
            return rd._get(path)

    def test_production_origin_is_fixed_https(self):
        self.assertEqual(rd.BASE, "https://market.ourolabs.ai")

    def test_off_origin_redirect_is_refused_and_never_followed(self):
        evil = self.serve(lambda p: (200, {}, b'{"buyers": [], "shops": []}'))
        origin = self.serve(lambda p: (302, {"Location": evil.url + "/api/board"}, b""))
        with self.assertRaises(Exception):
            self.get_via(origin)
        self.assertEqual(origin.hits, ["/api/board"])
        self.assertEqual(evil.hits, [])                                   # the redirect target was never contacted

    def test_same_origin_redirect_is_refused_too(self):
        origin = self.serve(lambda p: (302, {"Location": "/api/ledger?after=1"}, b"") if p == "/api/board"
                            else (200, {}, b"[]"))
        with self.assertRaises(Exception):
            self.get_via(origin)
        self.assertEqual(origin.hits, ["/api/board"])

    def test_credentials_redirect_is_refused(self):
        origin = self.serve(lambda p: (301, {"Location": "http://user:pw@127.0.0.1:1/api/board"}, b""))
        with self.assertRaises(Exception):
            self.get_via(origin)

    def test_plain_200_from_fixed_origin_still_works(self):
        origin = self.serve(lambda p: (200, {}, b'{"buyers": []}'))
        body, sha, n = self.get_via(origin)
        self.assertEqual((body, n), ({"buyers": []}, 14))

    def test_final_response_url_must_equal_the_requested_url(self):
        import io
        from unittest.mock import patch

        class Resp(io.BytesIO):
            def geturl(self):
                return "https://evil.example/api/board"
        with patch.object(rd._OPENER, "open", return_value=Resp(b"{}")):
            with self.assertRaises(ValueError):
                rd._get("/api/board")


class C3LedgerValidationTests(StateCase):
    def base(self):
        """A valid ledger as reserve() + mark_signed() write it: one signed reservation (t1) and one reserved (t2)."""
        OWNERS["b2"] = "o2"
        self.addCleanup(OWNERS.pop, "b2")
        self.evidence(POST, dict(POST, id=40002, author="b2", owner="o2"))
        self.assertEqual(self.reserve()[1], "reserved")
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20), "signed")
        self.assertEqual(self.reserve(post=40002, task="t2", unit=dict(UNIT2))[1], "reserved")
        return self.ledger()

    def corrupt_cases(self, led):
        def mut(fn):
            d = copy.deepcopy(led)
            fn(d["reservations"])
            return d
        r0 = lambda f: mut(lambda rs: f(rs[0]))
        return {
            "discount_negative": r0(lambda r: r.update(discount=-900, discounted_quote=18900)),
            "discount_string": r0(lambda r: r.update(discount="900")),
            "discount_missing": r0(lambda r: r.pop("discount")),
            "discount_bool": r0(lambda r: r.update(discount=True)),
            "discount_float": r0(lambda r: r.update(discount=900.0)),
            "discount_over_5pct": r0(lambda r: r.update(discount=1000, discounted_quote=17000)),
            "discounted_quote_incoherent": r0(lambda r: r.update(discounted_quote=17000)),
            "normal_quote_missing": r0(lambda r: r.pop("normal_quote")),
            "below_cost_plus_one": r0(lambda r: r.update(cost=17100)),
            "post_string": r0(lambda r: r.update(post="40001")),
            "task_missing": r0(lambda r: r.pop("task")),
            "unit_empty": r0(lambda r: r.update(unit="")),
            "sku_not_str": r0(lambda r: r.update(sku=5)),
            "owner_missing": r0(lambda r: r.pop("owner")),
            "settle_seq_bool": r0(lambda r: r.update(settle_seq=True)),
            "signed_without_price": r0(lambda r: r.pop("signed_price")),
            "signed_price_float": r0(lambda r: r.update(signed_price=17100.5)),
            "unknown_status": r0(lambda r: r.update(status="redeemed")),
            "dup_live_task": mut(lambda rs: rs[1].update(task="t1")),
            "dup_live_post": mut(lambda rs: rs[1].update(post=40001)),
            "dup_live_settle": mut(lambda rs: rs[1].update(settle_seq=rs[0]["settle_seq"])),
            "dup_live_author": mut(lambda rs: rs[1].update(author=rs[0]["author"])),
            "dup_live_owner": mut(lambda rs: rs[1].update(owner=rs[0]["owner"])),
            "dup_id": mut(lambda rs: rs[1].update(id=rs[0]["id"])),
            "over_hard_budget": mut(lambda rs: rs.extend(
                dict(copy.deepcopy(rs[1]), id=f"x{i}", task=f"x{i}", post=50000 + i, settle_seq=60000 + i,
                     author=f"a{i}", owner=f"o-{i}", normal_quote=200000, discount=10000, discounted_quote=190000)
                for i in range(2))),
            "reservations_not_list": {"pilot": rd.PILOT, "reservations": {}},
            "nan_cost": r0(lambda r: r.update(cost=float("nan"))),
        }

    def test_corrupt_ledger_fails_discount_closed_and_is_left_untouched(self):
        led = self.base()
        self.assertIsNotNone(rd.load_ledger(self.state))             # what the code itself writes is valid
        for name, bad in self.corrupt_cases(led).items():
            with self.subTest(name):
                raw = json.dumps(bad)                                  # NaN serialises as NaN: still loads
                (self.state / rd.LEDGER).write_text(raw, encoding="utf-8")
                self.assertIsNone(rd.load_ledger(self.state))
                self.assertEqual(self.reserve(post=40001, task="new")[1], "ledger_corrupt")
                self.assertEqual(rd.mark_signed(self.state, "t2", 18000, "u-next-2", "Gnext", T0 + 30), "ledger_corrupt")
                self.assertEqual(rd.release(self.state, "t2", "cancel", T0 + 30), "ledger_corrupt")
                self.assertEqual(rd.reconcile(self.state, [settle_row(9)], OWN, T0 + 300), [])
                self.assertEqual((self.state / rd.LEDGER).read_text(encoding="utf-8"), raw)   # never replaced

    def test_evidence_entry_must_be_complete_before_use(self):
        ev = json.loads((self.state / rd.EVIDENCE).read_text(encoding="utf-8"))
        for name, fn in {"owner_missing": lambda e: e.pop("owner"), "author_int": lambda e: e.update(author=5),
                         "settle_seq_str": lambda e: e.update(settle_seq="3605905"),
                         "owner_empty": lambda e: e.update(owner="")}.items():
            with self.subTest(name):
                bad = copy.deepcopy(ev)
                fn(bad["eligible"]["40001"])
                (self.state / rd.EVIDENCE).write_text(json.dumps(bad), encoding="utf-8")
                self.assertEqual(self.reserve()[1], "unverified")
        self.assertFalse((self.state / rd.LEDGER).exists())

    def test_refresh_never_replaces_a_corrupt_evidence_file(self):
        from test_review_discount import FakeMarket
        (self.state / "forum.json").write_text(json.dumps([POST]), encoding="utf-8")
        for raw in ("{broken", '{"version": 1, "eligible": []}'):
            with self.subTest(raw):
                (self.state / rd.EVIDENCE).write_text(raw, encoding="utf-8")
                m = FakeMarket()
                self.assertIsNone(rd.refresh_evidence(self.state, T0 + 10, own_urls=OWN, fetch=m))
                self.assertEqual((self.state / rd.EVIDENCE).read_text(encoding="utf-8"), raw)
                self.assertEqual(m.paths, [])


class C4AccountingTests(StateCase):
    def test_lower_negotiated_price_books_only_the_reserved_discount_labelled_upper_bound(self):
        res, _ = self.reserve()                                       # normal 18000, discount 900, opening 17100
        self.assertEqual(res["accounting"]["basis"], "opening_incentive_reservation_upper_bound")
        self.assertEqual(res["accounting"]["causal_cost"], "unknown")
        self.assertEqual(rd.mark_signed(self.state, "t1", 12000, "u-next-1", "Gnext", T0 + 20), "signed")
        got = rd.reconcile(self.state, [settle_row(9, amount=12000)], OWN, T0 + 300)
        r = got[0]
        self.assertEqual(rd.budget_view(self.ledger(), 10000)["committed"], 900)   # never 18000 - 12000
        self.assertEqual(r["opening_incentive_upper_bound"], 900)
        self.assertEqual(r["ordinary_concession_below_discounted_quote"], 5100)   # 17100 - 12000: NOT pilot cost
        self.assertNotIn("pilot_cost", r)
        rows = [json.loads(x) for x in (self.state / rd.JOURNAL).read_text(encoding="utf-8").splitlines()]
        done = next(x for x in rows if x["event"] == "committed")
        self.assertEqual((done["opening_incentive_upper_bound"], done["causal_cost"]), (900, "unknown"))


class _Sock:
    def __init__(self, closed=False):
        self._closed, self.timeouts = closed, []

    def settimeout(self, v):
        if self._closed:
            raise OSError(10038, "An operation was attempted on something that is not a socket")   # WinError 10038
        self.timeouts.append(v)


class _SlowResp:
    """Fake response: each read advances a fake monotonic clock by its delay. Exposes fp.raw._sock like HTTPResponse."""

    def __init__(self, clock, steps, sock):
        self.clock, self.steps = clock, list(steps)
        self.fp = type("FP", (), {"raw": type("Raw", (), {"_sock": sock})()})()

    def geturl(self):
        return "https://market.ourolabs.ai/api/board"

    def read(self, n=-1):
        delay, chunk = self.steps.pop(0)
        self.clock[0] += delay
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class C5DeadlineTests(unittest.TestCase):
    def get(self, steps, sock=None):
        from unittest.mock import patch
        clock, sock = [100.0], sock or _Sock()
        resp = _SlowResp(clock, steps, sock)
        with patch.object(rd._OPENER, "open", return_value=resp), patch.object(rd.time, "monotonic", lambda: clock[0]):
            return rd._get("/api/board"), sock

    def test_eof_returning_after_the_deadline_is_rejected(self):
        with self.assertRaises(TimeoutError):
            self.get([(0.1, b"{}"), (rd.GET_DEADLINE_S + 1, b"")])      # body complete, but EOF came back late

    def test_each_read_gets_the_remaining_budget_as_socket_timeout(self):
        (body, _, _), sock = self.get([(1.0, b"{"), (2.5, b"}"), (0.5, b"")])
        self.assertEqual(body, {})
        self.assertEqual(len(sock.timeouts), 3)
        self.assertEqual(sock.timeouts[0], rd.GET_DEADLINE_S)
        self.assertAlmostEqual(sock.timeouts[1], rd.GET_DEADLINE_S - 1.0)
        self.assertAlmostEqual(sock.timeouts[2], rd.GET_DEADLINE_S - 3.5)

    def test_already_closed_socket_is_skipped_not_winerror(self):
        (body, _, _), sock = self.get([(0.1, b"[]"), (0.1, b"")], sock=_Sock(closed=True))
        self.assertEqual(body, [])


class C6ReconcileTests(StateCase):
    def signed(self, at=T0 + 20):
        self.reserve()
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", at), "signed")

    def status(self):
        return self.ledger()["reservations"][0]

    def test_only_an_exact_round3_settle_after_signing_commits(self):
        def row(**kw):
            r = settle_row(9)
            r["data"].update({k: v for k, v in kw.items() if k not in ("round", "ts")})
            for k in ("round", "ts"):
                if k in kw:
                    r[k] = kw[k]
            for k, v in kw.items():
                if v is DROP:
                    r["data"].pop(k, None)
                    r.pop(k, None)
            return r
        near_misses = {
            "round_2": row(round=2),
            "round_missing": row(round=DROP),
            "settle_before_signing": row(ts=T0 + 19),
            "ts_missing": row(ts=DROP),
            "ts_string": row(ts=str(T0 + 100)),
            "amount_string": row(amount="17100"),
            "amount_nan": row(amount=float("nan")),
            "amount_zero_vs_zero": row(amount=0),
            "sku_missing_item_matches": row(sku=DROP),
            "item_differs": row(item="Gother"),
            "unit_missing": row(unit=DROP),
            "checkout_hash_missing": row(checkout_hash=DROP),
            "shop_http_variant": row(shop=OWN[0].replace("https://", "http://")),
        }
        for name, bad in near_misses.items():
            with self.subTest(name):
                self.setUp()
                self.signed()
                self.assertEqual(rd.reconcile(self.state, [bad], OWN, T0 + 300), [])
                self.assertEqual(self.status()["status"], "signed")

    def test_commit_records_the_real_rows_checkout_hash_and_states_the_task_join_is_absent(self):
        self.signed()
        good = settle_row(9)
        got = rd.reconcile(self.state, [good], OWN, T0 + 300)
        self.assertEqual(len(got), 1)
        r = self.status()
        self.assertEqual((r["status"], r["redeem_checkout_hash"], r["redeem_ts"]), ("committed", "hash-9", good["ts"]))
        self.assertTrue(r["task_join"].startswith("absent"))
        self.assertNotIn("callback", json.dumps(r))


class C7ProvenanceTests(StateCase):
    def setUp(self):
        super().setUp()
        (self.state / rd.EVIDENCE).unlink()
        (self.state / "forum.json").write_text(json.dumps([POST]), encoding="utf-8")

    def market(self, shop_owner):
        import test_review_discount as base
        board = copy.deepcopy(base.BOARD)
        if shop_owner is DROP:
            board["shops"][0].pop("owner")
        else:
            board["shops"][0]["owner"] = {"login": shop_owner}
        m = base.FakeMarket()
        orig = m.__call__

        def call(path):
            if path == "/api/board":
                m.paths.append(path)
                raw = json.dumps(board).encode()
                return board, rd.hashlib.sha256(raw).hexdigest(), len(raw)
            return orig(path)
        return call, m

    def test_config_owner_must_be_the_board_owner_of_this_shop(self):
        for owner in ("someone-else", DROP):
            with self.subTest(owner):
                (self.state / rd.EVIDENCE).unlink(missing_ok=True)
                call, m = self.market(owner)
                ev = rd.refresh_evidence(self.state, T0 + 10, own_urls=OWN, fetch=call)
                self.assertEqual(ev["eligible"], {})
                self.assertEqual(ev["rejected"]["40001"], "own_owner_unconfirmed")
                self.assertFalse(ev["own_owner_confirmed"])

    def test_confirmed_owner_and_retained_entries_are_explained(self):
        call, _ = self.market("kimchyoungman")
        ev = rd.refresh_evidence(self.state, T0 + 10, own_urls=OWN, fetch=call)
        self.assertTrue(ev["own_owner_confirmed"])
        self.assertIn("40001", ev["eligible"])
        call, _ = self.market("someone-else")                          # later refresh cannot confirm: kept, explained
        ev = rd.refresh_evidence(self.state, T0 + 100, own_urls=OWN, fetch=call)
        self.assertIn("40001", ev["eligible"])
        self.assertEqual(ev["eligible"]["40001"]["verified_at"], T0 + 10)
        for must in ("retained", "not re-verified", "not the opinion"):
            self.assertIn(must, ev["retained_note"])

    def test_reconcile_own_urls_come_from_the_registered_journal_only(self):
        self.config()
        import test_review_discount as base
        base_ev = rd.verify_post(POST, {REAL_SETTLE["seq"]: REAL_SETTLE}, OWN, OWNERS, "kimchyoungman")[0]
        (self.state / rd.EVIDENCE).write_text(json.dumps({"version": rd.EVIDENCE_VERSION,
                                                          "eligible": {"40001": base_ev}}), encoding="utf-8")
        self.reserve()
        rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20)
        cache_only = "https://cache-only.trycloudflare.com"
        cache = {"rows": [settle_row(77, shop=cache_only)], "own_urls": OWN + [cache_only]}
        (self.state / "public-settlements.json").write_text(json.dumps(cache), encoding="utf-8")
        out = rd.tick(self.state, T0 + 10 ** 6, own_urls=OWN, fetch=base.FakeMarket())
        self.assertEqual(out["committed"], 0)
        self.assertEqual(self.ledger()["reservations"][0]["status"], "signed")


def filler(lo, hi, skip=()):
    """Contiguous non-matching ledger rows lo..hi (order rows), minus `skip`."""
    return {s: {"seq": s, "ts": T0 + 50, "kind": "order", "round": 3, "data": {}} for s in range(lo, hi + 1)
            if s not in skip}


H = 3_700_000   # fixture ledger seqs sit above the reviewed deal's own settle seq (REAL_SETTLE 3605905)


class C8CursorScanTests(StateCase):
    def sign_with_cache_head(self, head=H + 1000):
        cache = {"cursor_seq": head, "rows": [], "own_urls": OWN}
        (self.state / "public-settlements.json").write_text(json.dumps(cache), encoding="utf-8")
        self.reserve()
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20), "signed")
        # later the newest-500 cache has moved far past the settle
        late = {"cursor_seq": H + 9000, "rows": list(filler(H + 8501, H + 9000).values()), "own_urls": OWN}
        (self.state / "public-settlements.json").write_text(json.dumps(late), encoding="utf-8")

    def r(self):
        return self.ledger()["reservations"][0]

    def tick(self, market, now=T0 + 10 ** 6):                         # long after expiry: reconciliation only
        return rd.tick(self.state, now, own_urls=OWN, fetch=market)

    def test_cursor_is_the_cache_head_at_signing(self):
        self.sign_with_cache_head(H + 1000)
        self.assertEqual(self.r()["scan_after"], H + 1000)

    def test_settle_beyond_the_newest_500_tail_is_ingested_by_cursor_pages(self):
        from test_review_discount import FakeMarket
        self.sign_with_cache_head(H + 1000)
        led = filler(H + 1001, H + 2500)
        led[H + 1200] = settle_row(H + 1200)
        m = FakeMarket(ledger=led)
        out = self.tick(m)
        self.assertEqual(out["committed"], 1)
        self.assertEqual((self.r()["status"], self.r()["redeem_seq"]), ("committed", H + 1200))
        self.assertEqual(m.paths[0], f"/api/ledger?after={H + 1000}")

    def test_scan_is_bounded_per_tick_and_resumes_from_the_persisted_cursor(self):
        from test_review_discount import FakeMarket
        self.sign_with_cache_head(H + 1000)
        led = filler(H + 1001, H + 4100)
        led[H + 4000] = settle_row(H + 4000)
        m = FakeMarket(ledger=led)
        self.assertEqual(self.tick(m)["committed"], 0)
        self.assertEqual(len(m.paths), rd.MAX_SCAN_PAGES)
        self.assertEqual((self.r()["status"], self.r()["scan_after"]), ("signed", H + 1000 + 500 * rd.MAX_SCAN_PAGES))
        self.assertEqual(self.tick(m)["committed"], 1)
        self.assertEqual(self.r()["redeem_seq"], H + 4000)

    def test_seq_gap_after_signing_is_an_explicit_unknown_hold(self):
        from test_review_discount import FakeMarket
        self.sign_with_cache_head(H + 1000)
        m = FakeMarket(ledger=filler(H + 1001, H + 1300, skip=range(H + 1001, H + 1101)))   # H + 1001..1100 unobservable
        self.assertEqual(self.tick(m)["committed"], 0)
        r = self.r()
        self.assertEqual(r["status"], "unknown")
        self.assertEqual(r["unknown_gaps"], [[H + 1001, H + 1100]])
        self.assertNotIn("redeem_seq", r)
        self.assertNotEqual(rd.release(self.state, "t1", "cancel", T0 + 10 ** 6), "released")
        self.assertEqual(self.r()["status"], "unknown")
        self.assertEqual(rd.budget_view(self.ledger(), 10000), {"reserved": 900, "committed": 0, "available": 9100})
        self.config(activated_at=T0 + 10 ** 6, expires_at=T0 + 10 ** 6 + 3600)
        self.assertEqual(self.reserve(task="t9", now=T0 + 10 ** 6 + 1)[1], "already_used")   # post stays locked

    def test_fetch_failure_keeps_signed_and_cursor(self):
        from test_review_discount import FakeMarket
        self.sign_with_cache_head(H + 1000)
        self.tick(FakeMarket(fail=TimeoutError("slow")))
        self.assertEqual((self.r()["status"], self.r()["scan_after"]), ("signed", H + 1000))

    def test_no_signed_reservation_means_no_ledger_scan(self):
        from test_review_discount import FakeMarket
        self.reserve()
        m = FakeMarket()
        self.tick(m)
        self.assertEqual(m.paths, [])


DROP = object()
UNIT2 = {"unit": "u-next-2", "sku": "Gnext", "cost": 9000, "list_price": 20000}


class A1A3SignGuardTests(StateCase):
    """additional-review.txt 1 and 3, review_discount side: mark_signed is the last gate before a discounted receipt."""

    def test_mark_signed_after_expiry_is_refused_and_stays_unsigned(self):
        self.reserve()
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 86400), "expired")
        self.assertEqual(self.ledger()["reservations"][0]["status"], "reserved")
        self.assertEqual(rd.release(self.state, "t1", "mark_signed:expired", T0 + 86400), "released")

    def test_mark_signed_honours_integer_cost_plus_one_of_the_fresh_unit(self):
        self.reserve()
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20, cost=17100), "mismatch")
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20, cost=17100.5), "mismatch")
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20, cost=float("nan")),
                         "mismatch")
        self.assertEqual(self.ledger()["reservations"][0]["status"], "reserved")
        self.assertEqual(rd.mark_signed(self.state, "t1", 17100, "u-next-1", "Gnext", T0 + 20, cost=17099.5), "signed")


import test_seller_review_discount as tsr   # noqa: E402  (module import: its TestCase is not re-collected here)

_B = tsr.SellerReviewDiscountTests


class SellerCorrectionTests(unittest.IsolatedAsyncioTestCase):
    """The REAL staged seller path (A2A JSON-RPC over httpx ASGI, deterministic fallback, fake signer)."""
    asyncSetUp, asyncTearDown, send, cancel = _B.asyncSetUp, _B.asyncTearDown, _B.send, _B.cancel
    reply, ledger, rows = _B.reply, _B.ledger, _B.rows

    def inventory(self, **unit):
        Path(self.tmp, "inventory.json").write_text(json.dumps({"round": 3, "units": [dict(tsr.UNIT, **unit)]}),
                                                    encoding="utf-8")

    async def insist(self, offer, task, turns=40):
        for _ in range(turns):
            task = await self.send(str(offer), task["id"])
            if task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
                break
        return task

    # --- additional-review 1: expiry
    async def test_unsigned_coupon_task_is_rejected_after_expiry_not_signed_or_repriced(self):
        cfg = json.loads(Path(self.tmp, rd.CONFIG).read_text(encoding="utf-8"))
        Path(self.tmp, rd.CONFIG).write_text(json.dumps(dict(cfg, expires_at=self.t + 60)), encoding="utf-8")
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        self.assertIn("asking 17100", self.reply(a))
        self.t += 61                                                   # window closed, reservation not yet stale
        b = await self.send("17100", a["id"])
        self.assertEqual(b["status"]["state"], "TASK_STATE_REJECTED")
        self.assertEqual(self.signed, [])
        self.assertEqual([(r["status"], r["released_why"]) for r in self.ledger()], [("released", "expired")])
        self.assertEqual(self.rows("counter"), [])                     # no silent re-quote at the regular price

    # --- additional-review 3: integer cost+1 with a fractional or risen cost
    async def test_fractional_cost_keeps_every_discounted_quote_an_int_at_or_above_floor_plus_one(self):
        self.inventory(cost=9000.5)
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        await self.insist(9000, a)
        quotes = [r["quote"] for r in self.rows("counter")]
        self.assertTrue(quotes and all(type(q) is int and q >= 9001 for q in quotes), quotes)
        self.assertTrue(all(s["price"] >= 9001 for s in self.signed))

    async def test_cost_rise_before_signing_never_signs_below_fresh_cost_plus_one(self):
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        self.inventory(cost=17150)
        await self.send("17100", a["id"])
        self.assertTrue(all(s["price"] >= 17151 for s in self.signed), self.signed)

    # --- additional-review 4: accept below the pilot floor converts to a counter at the floor, not an accept
    async def test_buyer_insisting_on_cost_gets_a_floor_counter_and_can_then_close_at_floor(self):
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        a = await self.insist(9000, a)
        self.assertEqual(a["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertEqual(self.rows("counter")[-1]["quote"], 9001)
        self.assertEqual(self.rows("review_discount_unsigned"), [])
        self.assertEqual([r["status"] for r in self.ledger()], ["reserved"])
        done = await self.send("9001", a["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(self.signed[0]["price"], 9001)

    # --- independent verification (expected to pass before and after the fixes)
    async def test_verify_fallback_asks_with_coupon_never_exceed_the_reserved_discount_below_regular(self):
        Path(self.tmp, "inventory.json").write_text(
            json.dumps({"round": 3, "units": [tsr.UNIT, dict(tsr.UNIT, unit="u-rd-2")]}), encoding="utf-8")
        c = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        n = await self.send("I want Review desk Grd1")
        for _ in range(12):
            c, n = await self.send("1", c["id"]), await self.send("1", n["id"])
        q = lambda tid: [r["quote"] for r in self.rows("first_quote") + self.rows("counter") if r["task"] == tid]
        disc = self.ledger()[0]["discount"]
        self.assertEqual(len(q(c["id"])), 13)
        for qc, qn in zip(q(c["id"]), q(n["id"])):
            self.assertLessEqual(qn - qc, disc)                         # pilot-specific concession <= the reserved 5%
            self.assertGreaterEqual(qc, 9001)

    async def test_verify_model_counter_far_below_opening_is_clamped_and_books_only_the_discount(self):
        from unittest.mock import patch
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        with patch.object(tsr.seller_r3, "ask_json", return_value=({"action": "counter", "price": 5000}, "")):
            a = await self.send("5000", a["id"])
        self.assertEqual(self.rows("counter")[-1]["quote"], 9001)      # clamped to cost+1, never below
        with patch.object(tsr.seller_r3, "ask_json", return_value=({"action": "accept"}, "")):
            await self.send("9001", a["id"])
        self.assertEqual(self.signed[0]["price"], 9001)
        r = self.ledger()[0]
        self.assertEqual((r["status"], r["discount"], r["accounting"]["causal_cost"]), ("signed", 900, "unknown"))

    async def test_verify_semantically_corrupt_ledger_means_regular_price_and_sales_continue(self):
        bad = {"pilot": rd.PILOT, "reservations": [{"status": "reserved", "discount": -5000}]}
        Path(self.tmp, rd.LEDGER).write_text(json.dumps(bad), encoding="utf-8")
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        self.assertIn("asking 18000", self.reply(a))
        done = await self.send("18000", a["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(json.loads(Path(self.tmp, rd.LEDGER).read_text(encoding="utf-8")), bad)   # untouched

    async def test_verify_signed_reservation_survives_stale_sweep_cancel_and_expiry(self):
        a = await self.send("I want Review desk Grd1. REVIEW-COUPON 40001")
        await self.send("17100", a["id"])
        self.assertEqual([r["status"] for r in self.ledger()], ["signed"])
        self.t += 86400 + tsr.seller_r3.RESERVATION_TTL + 1
        self.assertEqual(rd.release(self.tmp, a["id"], "cancel", self.t), "signed_kept")
        await self.send("I want Review desk Grd1")                      # runs the sweep; regular path
        self.assertEqual([r["status"] for r in self.ledger()], ["signed"])


if __name__ == "__main__":
    unittest.main()
