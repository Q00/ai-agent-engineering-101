"""Week 13 tests. Run: python -m unittest -v test_week13 (from this folder)."""
import json
import os
import tempfile
import unittest

import mock_payment
import contract
import trade
import verify


def agree():
    return contract.negotiate(item="우유 190mL x 24", qty=1, list_price=12000,
                              seller_floor=9000, buyer_budget=10000)


class Negotiation(unittest.TestCase):
    def test_agreement_lands_between_floor_and_budget(self):
        n = agree()
        self.assertEqual(n["agreement"]["unit_price"], 10000)
        self.assertEqual(n["agreement"]["total"], 10000)
        self.assertEqual(n["messages"][-1]["act"], "accept")

    def test_no_agreement_when_budget_is_under_the_floor(self):
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=20000,
                               seller_floor=15000, buyer_budget=14000)
        self.assertIsNone(n["agreement"])
        self.assertEqual(n["messages"][-1]["act"], "walk_away")

    def test_a_wide_gap_still_closes_when_the_floor_is_under_the_budget(self):
        # First real run: this pair stopped after 10 rounds at 17500 against 17000.
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=22000,
                               seller_floor=15000, buyer_budget=17000)
        self.assertEqual(n["agreement"]["unit_price"], 17000)

    def test_walking_away_is_the_turn_after_the_last_offer(self):
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=20000,
                               seller_floor=15000, buyer_budget=14000)
        self.assertEqual(n["messages"][-1]["turn"], n["messages"][-2]["turn"] + 1)

    def test_a_quantity_under_one_is_refused(self):
        for qty in (0, -1):
            with self.assertRaises(ValueError):
                contract.negotiate(item="우유", qty=qty, list_price=12000, seller_floor=9000, buyer_budget=10000)

    def test_total_is_unit_price_times_quantity(self):
        n = contract.negotiate(item="생수 2L x 6", qty=3, list_price=12000,
                               seller_floor=9000, buyer_budget=10000)
        self.assertEqual(n["agreement"]["total"], 30000)


class Contract(unittest.TestCase):
    def test_sealed_wrapper_is_what_the_payment_layer_accepts(self):
        w = contract.seal(agree(), contract_id="ctr-test-1", created_at=1795520000)
        self.assertEqual(w["body"]["schema"], "meshkit.transaction-contract/v1")
        self.assertEqual(w["hash"], mock_payment.digest(w["body"]))
        self.assertEqual(w["body"]["offer"]["total"], 10000)
        self.assertEqual(w["body"]["parties"]["seller"], "nocha12")
        receipt = mock_payment.settle_and_receipt(mock_payment.Ledger(), w)
        self.assertEqual(receipt["contract_id"], "ctr-test-1")

    def test_both_parties_tags_verify_and_a_changed_body_does_not(self):
        w = contract.seal(agree(), contract_id="ctr-test-1", created_at=1795520000)
        self.assertEqual(contract.check_wrapper(w), [])
        w["body"]["offer"]["total"] = 1
        self.assertNotEqual(contract.check_wrapper(w), [])

    def test_no_contract_without_an_agreement(self):
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=20000,
                               seller_floor=15000, buyer_budget=14000)
        with self.assertRaises(ValueError):
            contract.seal(n, contract_id="ctr-x", created_at=1)


MANDATE = {"auto_limit": 20000, "trusted": ["nocha12"], "blocked": ["박리다매"]}


class TradeCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "trade-record.jsonl")
        self.record = trade.Record(self.path)

    def run_trade(self, cid, qty=1, mandate=MANDATE, confirm=None):
        n = contract.negotiate(item="우유 190mL x 24", qty=qty, list_price=12000,
                               seller_floor=9000, buyer_budget=10000)
        return trade.run_trade(self.record, mandate, n, contract_id=cid, confirm=confirm)

    def raw(self):
        with open(self.path, "rb") as f:
            return f.read()

    def lines(self, cid=None):
        return [l for l in self.record.read() if cid is None or l["trade"] == cid]


class EndToEnd(TradeCase):
    def test_small_trusted_trade_runs_to_settled_without_a_person(self):
        out = self.run_trade("ctr-a")
        self.assertEqual((out["route"], out["state"]), ("auto", "settled"))
        kinds = [l["type"] for l in self.lines("ctr-a") if l["type"] != "state"]
        self.assertEqual(kinds, ["negotiation", "contract", "routing", "ledger", "event"])
        states = [l["data"]["to"] for l in self.lines("ctr-a") if l["type"] == "state"]
        self.assertEqual(states, ["created", "paid", "settled"])

    def test_settlement_posts_three_entries_that_sum_to_zero(self):
        self.run_trade("ctr-a")
        led = [l for l in self.lines("ctr-a") if l["type"] == "ledger"][0]["data"]
        moves = {e["acct"]: e["delta"] for e in led["entries"]}
        self.assertEqual(moves, {"user": -10000, "seller": 9700, "platform": 300})
        self.assertEqual(led["balances"], {"user": -10000, "seller": 9700, "platform": 300})
        self.assertEqual(led["sum"], 0)

    def test_event_points_at_the_contract_and_carries_a_valid_platform_tag(self):
        self.run_trade("ctr-a")
        wrapper = [l for l in self.lines("ctr-a") if l["type"] == "contract"][0]["data"]
        event = dict([l for l in self.lines("ctr-a") if l["type"] == "event"][0]["data"])
        self.assertEqual(event["schema"], "meshkit.transaction-event/v1")
        self.assertEqual((event["contract_id"], event["contract_hash"]), ("ctr-a", wrapper["hash"]))
        tag = event.pop("platform_auth_tag")
        self.assertEqual(tag, mock_payment.auth_tag(event))

    def test_over_the_limit_asks_a_person_and_yes_settles(self):
        asked = []
        out = self.run_trade("ctr-big", qty=3, confirm=lambda q: asked.append(q) or "y")
        self.assertEqual((out["route"], out["state"]), ("confirm", "settled"))
        self.assertEqual(len(asked), 1)
        routing = [l for l in self.lines("ctr-big") if l["type"] == "routing"][0]["data"]
        self.assertEqual(routing["human"]["answer"], "y")

    def test_over_the_limit_and_no_cancels_with_no_money_moved(self):
        out = self.run_trade("ctr-big", qty=3, confirm=lambda q: "n")
        self.assertEqual((out["route"], out["state"]), ("confirm", "cancelled"))
        self.assertEqual([l for l in self.lines() if l["type"] in ("ledger", "event")], [])

    def test_confirm_route_without_a_person_does_not_pay(self):
        out = self.run_trade("ctr-big", qty=3, confirm=None)
        self.assertEqual(out["state"], "cancelled")

    def test_blocked_seller_is_rejected_before_any_payment(self):
        out = self.run_trade("ctr-a", mandate={"auto_limit": 20000, "trusted": [], "blocked": ["nocha12"]})
        self.assertEqual((out["route"], out["state"]), ("reject", "cancelled"))
        self.assertEqual([l for l in self.lines() if l["type"] == "ledger"], [])

    def test_no_agreement_leaves_only_the_negotiation(self):
        n = contract.negotiate(item="쌀 5kg", qty=1, list_price=20000, seller_floor=15000, buyer_budget=14000)
        out = trade.run_trade(self.record, MANDATE, n, contract_id="ctr-none")
        self.assertEqual(out["state"], "no_deal")
        self.assertEqual([l["type"] for l in self.lines()], ["negotiation"])

    def test_a_contract_id_cannot_be_used_twice(self):
        self.run_trade("ctr-a")
        with self.assertRaises(ValueError):
            self.run_trade("ctr-a")

    def test_a_second_run_leaves_earlier_lines_untouched(self):
        self.run_trade("ctr-a")
        before = self.raw()
        self.run_trade("ctr-b")
        self.assertTrue(self.raw().startswith(before))


class Refund(TradeCase):
    def setUp(self):
        super().setUp()
        self.run_trade("ctr-a")
        self.run_trade("ctr-b")

    def test_refund_adds_opposite_entries_and_keeps_the_original(self):
        trade.refund(self.record, "ctr-b")
        led = [l["data"] for l in self.lines("ctr-b") if l["type"] == "ledger"]
        self.assertEqual(len(led), 2)
        self.assertEqual({e["acct"]: e["delta"] for e in led[0]["entries"]},
                         {"user": -10000, "seller": 9700, "platform": 300})
        self.assertEqual({e["acct"]: e["delta"] for e in led[1]["entries"]},
                         {"user": 10000, "seller": -9700, "platform": -300})
        self.assertEqual(led[1]["sum"], 0)

    def test_refund_moves_the_trade_to_refunded_and_emits_an_event(self):
        out = trade.refund(self.record, "ctr-b")
        self.assertEqual(out["state"], "refunded")
        events = [l["data"]["event"] for l in self.lines("ctr-b") if l["type"] == "event"]
        self.assertEqual(events, ["settled", "refunded"])

    def test_balances_return_to_what_they_were_before_the_refunded_trade(self):
        out = trade.refund(self.record, "ctr-b")
        # ctr-a stays settled, so "before ctr-b" is not zero.
        self.assertEqual(out["check"]["user_before_trade"], -10000)
        self.assertEqual(out["check"]["user_after_refund"], -10000)
        self.assertEqual(out["check"]["seller_before_trade"], 9700)
        self.assertEqual(out["check"]["seller_after_refund"], 9700)
        self.assertTrue(out["check"]["match"])

    def test_refunding_an_older_trade_sets_aside_what_later_trades_moved(self):
        # Review C1: ctr-b settled after ctr-a, so the plain before/after numbers differ.
        out = trade.refund(self.record, "ctr-a")
        c = out["check"]
        self.assertEqual((c["user_before_trade"], c["user_after_refund"]), (0, -10000))
        self.assertEqual((c["user_other_trades_since"], c["seller_other_trades_since"]), (-10000, 9700))
        self.assertTrue(c["match"])
        self.assertEqual(verify.verify(self.path, MANDATE)["problems"], [])

    def test_a_refund_that_stopped_half_way_is_not_posted_again(self):
        # Review C2: the process dies after the refund entries, before the state line.
        trade.refund(self.record, "ctr-b")
        lines = self.raw().splitlines(keepends=True)
        last_ledger = max(i for i, l in enumerate(lines) if b'"type": "ledger"' in l)
        with open(self.path, "wb") as f:
            f.writelines(lines[:last_ledger + 1])
        with self.assertRaises(trade.IllegalTransition):
            trade.refund(self.record, "ctr-b")
        self.assertEqual(len([l for l in self.lines("ctr-b") if l["type"] == "ledger"]), 2)

    def test_a_trade_that_stopped_at_paid_cannot_be_refunded(self):
        lines = self.raw().splitlines(keepends=True)
        with open(self.path, "wb") as f:
            f.writelines(lines[:6])   # ctr-a: up to its ledger line, no event, no settled
        with self.assertRaises(trade.IllegalTransition):
            trade.refund(self.record, "ctr-a")

    def test_a_trade_is_refunded_once(self):
        trade.refund(self.record, "ctr-b")
        with self.assertRaises(trade.IllegalTransition):
            trade.refund(self.record, "ctr-b")

    def test_a_cancelled_trade_cannot_be_refunded(self):
        self.run_trade("ctr-big", qty=3, confirm=lambda q: "n")
        with self.assertRaises(trade.IllegalTransition):
            trade.refund(self.record, "ctr-big")

    def test_a_settled_trade_cannot_go_back_to_cancelled(self):
        with self.assertRaises(trade.IllegalTransition):
            trade.move(self.record, "ctr-a", "cancelled", "undo")


class Replay(TradeCase):
    """verify.py gets the record file and nothing else."""

    def setUp(self):
        super().setUp()
        self.run_trade("ctr-a")
        self.run_trade("ctr-b")
        trade.refund(self.record, "ctr-b")
        self.run_trade("ctr-yes", qty=3, confirm=lambda q: "y")
        self.run_trade("ctr-no", qty=3, confirm=lambda q: "n")

    def forge(self, mutate):
        """Change the record the way a careful forger would: edit, then rebuild
        every seq, prev and hash so the chain itself still holds."""
        lines = self.record.read()
        mutate(lines)
        prev = trade.GENESIS
        with open(self.path, "w", encoding="utf-8") as f:
            for i, l in enumerate(lines):
                l["seq"], l["prev"] = i + 1, prev
                l["hash"] = prev = trade.line_hash(l)
                f.write(json.dumps(l, ensure_ascii=False, sort_keys=True) + "\n")

    def problems(self):
        return verify.verify(self.path, MANDATE)["problems"]

    def pick(self, lines, cid, kind, n=0):
        return [l for l in lines if l["trade"] == cid and l["type"] == kind][n]

    def test_an_honest_record_replays_clean(self):
        report = verify.verify(self.path, MANDATE)
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["head"], self.record.read()[-1]["hash"])
        self.assertEqual(report["trades"], {"ctr-a": "settled", "ctr-b": "refunded",
                                            "ctr-yes": "settled", "ctr-no": "cancelled"})
        self.assertEqual(report["balances"], {"user": -40000, "seller": 38800, "platform": 1200})

    def test_an_edited_line_breaks_the_chain(self):
        raw = self.raw().replace(b'"delta": 9700', b'"delta": 9900', 1)
        with open(self.path, "wb") as f:
            f.write(raw)
        self.assertTrue(any("hash" in p for p in self.problems()))

    def test_a_removed_line_breaks_the_chain(self):
        kept = self.raw().splitlines(keepends=True)
        with open(self.path, "wb") as f:
            f.writelines(kept[:5] + kept[6:])
        self.assertTrue(any("removed or reordered" in p for p in self.problems()))

    def test_forged_contract_terms_do_not_match_the_contract_hash(self):
        def cheaper(lines):
            self.pick(lines, "ctr-a", "contract")["data"]["body"]["offer"]["total"] = 100
        self.forge(cheaper)
        self.assertTrue(any("contract hash mismatch" in p for p in self.problems()))

    def test_forged_ledger_entry_is_caught_by_the_amounts(self):
        def skim(lines):
            entries = self.pick(lines, "ctr-a", "ledger")["data"]["entries"]
            next(e for e in entries if e["acct"] == "seller")["delta"] = 9900
        self.forge(skim)
        self.assertTrue(any("ctr-a" in p and "entries" in p for p in self.problems()))

    def test_forged_event_fails_the_platform_tag(self):
        def inflate(lines):
            self.pick(lines, "ctr-a", "event")["data"]["amount"] = 99999
        self.forge(inflate)
        self.assertTrue(any("platform tag" in p for p in self.problems()))

    def test_payment_after_a_no_is_caught(self):
        def flip(lines):
            self.pick(lines, "ctr-yes", "routing")["data"]["human"]["answer"] = "n"
        self.forge(flip)
        self.assertTrue(any("ctr-yes" in p and "approv" in p for p in self.problems()))

    def test_a_routing_decision_the_mandate_does_not_give_is_caught(self):
        def wave_through(lines):
            d = self.pick(lines, "ctr-yes", "routing")["data"]
            d["decision"], d["human"] = "auto", None
        self.forge(wave_through)
        self.assertTrue(any("ctr-yes" in p and "routing" in p for p in self.problems()))

    def test_an_erased_refund_is_caught(self):
        def erase(lines):
            lines[:] = [l for l in lines if not (l["trade"] == "ctr-b" and l["type"] == "ledger"
                                                 and "refund_of_seq" in l["data"])]
        self.forge(erase)
        self.assertTrue(any("ctr-b" in p and "refunded trade should hold" in p for p in self.problems()))

    def test_a_cut_off_tail_is_caught_against_the_published_head(self):
        # Review I1: dropping whole trades from the end keeps the chain intact.
        head = self.record.read()[-1]["hash"]
        kept = self.raw().splitlines(keepends=True)
        with open(self.path, "wb") as f:
            f.writelines(kept[:8])
        self.assertEqual(verify.verify(self.path, MANDATE)["problems"], [])
        self.assertTrue(any("head" in p for p in verify.verify(self.path, MANDATE, head=head)["problems"]))

    def test_a_mandate_swapped_inside_the_record_is_caught(self):
        # Review C3: raise the limit in the routing line and call the route "auto".
        def loosen(lines):
            d = self.pick(lines, "ctr-yes", "routing")["data"]
            d["mandate"]["auto_limit"], d["decision"], d["human"] = 10 ** 9, "auto", None
        self.forge(loosen)
        self.assertTrue(any("ctr-yes" in p and "published mandate" in p for p in self.problems()))

    def test_a_rewritten_agreement_text_is_caught(self):
        def rewrite(lines):
            a = self.pick(lines, "ctr-a", "negotiation")["data"]["agreement"]
            a["text"], a["seller"] = "user-agent buys 100 x gold from mallory at 1 credit", "mallory"
        self.forge(rewrite)
        self.assertTrue(any("ctr-a" in p and "agreement" in p for p in self.problems()))

    def test_a_cancelled_trade_whose_person_said_yes_is_caught(self):
        def flip(lines):
            self.pick(lines, "ctr-no", "routing")["data"]["human"]["answer"] = "y"
        self.forge(flip)
        self.assertTrue(any("ctr-no" in p for p in self.problems()))

    def test_fractional_or_stray_ledger_amounts_are_caught(self):
        def blur(lines):
            entries = self.pick(lines, "ctr-a", "ledger")["data"]["entries"]
            entries[0]["delta"] = float(entries[0]["delta"])
        self.forge(blur)
        self.assertTrue(any("ctr-a" in p and "whole" in p for p in self.problems()))

    def test_a_broken_file_is_reported_not_crashed_on(self):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        self.assertTrue(any("not JSON" in p for p in self.problems()))

    def test_an_empty_record_is_not_verified(self):
        open(self.path, "w").close()
        self.assertNotEqual(self.problems(), [])

    def test_an_illegal_state_move_is_caught(self):
        def reopen(lines):
            last = self.pick(lines, "ctr-no", "state", -1)["data"]
            last["to"] = "settled"
        self.forge(reopen)
        self.assertTrue(any("ctr-no" in p for p in self.problems()))


if __name__ == "__main__":
    unittest.main()
