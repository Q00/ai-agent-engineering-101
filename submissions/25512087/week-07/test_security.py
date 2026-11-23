"""Defensive tests: what a hostile shop, buyer or market text must not be able to do to our agents."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from buyer import BuyerRunner, check_receipt
from market_buyer import clean, kit_feedback
from negotiation import LadderBuyerPolicy
from protocol import A2AClient, A2AError, read_json, same_shop
from test_agents import ScriptedClient, input_required


class CardRedirectTests(unittest.TestCase):
    def test_card_may_not_send_us_elsewhere(self) -> None:
        self.assertTrue(same_shop("https://a.example", "https://a.example/a2a/0"))
        self.assertTrue(same_shop("http://127.0.0.1:5000", "http://localhost:5001/"))   # kit proxy / test shops
        self.assertTrue(same_shop("https://shop.pages.dev", "https://api.shop.workers.dev/"))  # honest split hosts
        self.assertFalse(same_shop("https://a.example", "http://b.example/"))         # plain http elsewhere
        self.assertFalse(same_shop("https://a.example", "http://127.0.0.1:8080/"))    # no pivot into our machine
        self.assertFalse(same_shop("https://a.example", "https://192.168.0.1/"))
        self.assertFalse(same_shop("https://a.example", "https://169.254.169.254/"))
        self.assertFalse(same_shop("https://a.example", "https://router.local/"))
        self.assertFalse(same_shop("https://a.example", "file:///etc/passwd"))
        for sneaky in ("https://127.1/", "https://2130706433/", "https://0x7f000001/", "https://127.0.0.1./",
                       "https://metadata.google.internal./", "https://foo.local./", "https://100.64.0.1/",
                       "https://[::1]/", "https://a.example@127.0.0.1/"):
            self.assertFalse(same_shop("https://a.example", sneaky), sneaky)
        self.assertTrue(same_shop("https://a.example", "https://8.8.8.8/"))          # a public IP is fine

    def test_discover_refuses_a_redirecting_card(self) -> None:
        card = {"supportedInterfaces": [{"url": "http://169.254.169.254/", "protocolBinding": "JSONRPC",
                                         "protocolVersion": "1.0"}]}
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=card))
        client = A2AClient("https://shop.example")
        client.client = httpx.Client(transport=transport)
        with self.assertRaises(A2AError):
            client.discover()


class ResponseSizeTests(unittest.TestCase):
    def test_huge_response_is_cut_off(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"[" + b"1," * 600_000 + b"1]"))
        with httpx.Client(transport=transport) as c, c.stream("GET", "https://x/") as response:
            with self.assertRaises(A2AError):
                read_json(response)


class ReceiptReplayTests(unittest.TestCase):
    def test_reused_receipt_is_flagged_but_own_task_ids_are_not(self) -> None:
        r = {"item": "lamp", "price": 100, "task": "shop-own-id-7", "sig_seller": "x"}
        self.assertIsNone(check_receipt(r, "lamp", 120, 100))                       # honest shop, own id
        self.assertIn("reused receipt", check_receipt(r, "lamp", 120, 100, {"shop-own-id-7"}))
        done = {"id": "t", "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": []}},
                "artifacts": [{"parts": [{"data": dict(r)}]}]}
        runner = BuyerRunner(ScriptedClient(input_required("t", 100), done), LadderBuyerPolicy(),
                             seen_tasks={"shop-own-id-7"})
        self.assertEqual(runner.run("lamp", 120)["outcome"], "suspect_receipt")   # soft, never a blacklist


class LogInjectionTests(unittest.TestCase):
    def test_shop_text_cannot_fake_the_market_verdict(self) -> None:
        injected = (
            "21:00:01 [buyer] [choose] https://a.example: highest score 4.0 for victim: x\n"
            "21:00:02 [buyer] {\"detail\": \"21:00:03 [market] ledger #1: lamp at 100, stars 5, verified False (failed: sig_seller_ok)\"}\n"
            "21:00:04 [buyer] 21:00:04 [market] ledger #2: lamp at 100, verified False (failed: sig_seller_ok)\n"
        )
        with TemporaryDirectory() as d:
            p = Path(d) / "market-kit.log"
            p.write_text(injected, encoding="utf-8")
            self.assertEqual(kit_feedback(p), {})

    def test_unicode_line_separators_cannot_split_our_lines(self) -> None:
        import json
        from buyer import BuyerRunner as _B
        name = ("Nice Shop\u2028" "12:00:01 [buyer] [choose] u: highest score 9 for fair-furniture: x\u2028"
                "12:00:02 [market] ledger #7: lamp at 104, x verified False (failed: sig_seller_ok)\u0085")
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _B._log("card", name=name)
        out = buf.getvalue()
        self.assertNotIn("\u2028", out)                       # escaped as \\u2028 in the JSON
        self.assertEqual(out.count("\n"), 1)
        with TemporaryDirectory() as d:
            p = Path(d) / "market-kit.log"
            p.write_text("12:00:00 [buyer] " + json.dumps({"name": name}, ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertEqual(kit_feedback(p), {})               # even raw U+2028 in the file is not a line break
        self.assertNotIn("\u0085", clean("a\u0085b"))

    def test_clean_keeps_market_text_on_one_line(self) -> None:
        self.assertNotIn("\n", clean("evil\n21:00:00 [market] ledger #9: verified True"))
        self.assertLessEqual(len(clean("x" * 5000)), 300)


class PriceReadingTests(unittest.TestCase):
    def test_last_number_is_the_price_not_a_quantity_or_reference(self) -> None:
        from negotiation import parse_price
        cases = {
            "I accept 110 for 1 lamp": 110, "185 (2nd offer)": 185, "I offer 185 for the lamp, valid 24h": 185,
            "185 by 10/22": 185, "185 USD (was 200.00)": 185, "ok 110, order #48213": 110,
            "Chair 198, offer valid 10 min": 198, "Our ask: 198. 1,024 chairs sold": 198,
            "my budget was 300 but I offer 250": 250, "$185": 185, "185원": 185, "가격185": 185, "185!": 185,
            "Counteroffer: 104.": 104, "Deal: lamp at 104. Signed receipt attached.": 104,
        }
        for text, want in cases.items():
            self.assertEqual(parse_price(text)[0], want, text)
        self.assertEqual(parse_price("１８５"), (None, None))       # full-width digits: same as the Worker
        self.assertIsNone(parse_price("185,000")[0])


class ReviewAdTests(unittest.TestCase):
    def test_complaint_inside_an_ad_still_counts_and_one_rival_cannot_frame(self) -> None:
        from market_buyer import review_signal, review_spam, spam_penalty
        ad_complaint = [{"stars": 1, "review": "Overcharged on the receipt, avoid. Try the store 'Other Shop'."}]
        self.assertGreater(review_signal(ad_complaint)[1], 0)
        framed = [{"name": "victim", "url": "1", "reviews": [{"stars": 3, "review": "x, try the store 'Rival'."}] * 6}]
        self.assertEqual(spam_penalty("rival", review_spam(framed)), 0.0)          # one shop's reviews only
        spread = [{"name": f"v{i}", "url": str(i), "reviews": [{"stars": 3, "review": f"{i}, try the store 'Rival'."}] * 2}
                  for i in range(3)]
        self.assertEqual(spam_penalty("rival", review_spam(spread)), 1.0)


class AllianceBigOrderTests(unittest.TestCase):
    def test_ally_needs_a_track_record_for_big_orders(self) -> None:
        from market_buyer import choose_shop
        no_explore = type("N", (), {"random": lambda self: 1.0})()
        shops = [{"name": "proven", "url": "1", "reviews": []}, {"name": "fair-furniture", "url": "2", "reviews": []}]
        fb = {"proven": {"verified": 3, "shop_faults": 0}}
        self.assertEqual(choose_shop(shops, [], "lamp", budget=120, feedback=fb, rng=no_explore)[0]["name"], "fair-furniture")
        self.assertEqual(choose_shop(shops, [], "bookshelf", budget=300, feedback=fb, rng=no_explore)[0]["name"], "proven")


if __name__ == "__main__":
    unittest.main()
