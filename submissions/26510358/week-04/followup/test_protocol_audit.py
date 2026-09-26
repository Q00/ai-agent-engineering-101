"""The strict pending-offer interpretation is a separate protocol choice."""
import unittest

from protocol_audit import Event, parse_log, replay_all, simulate


class PendingOfferTests(unittest.TestCase):
    def test_reject_closes_offer_for_strict_mode(self):
        events = [Event("buyer", "propose", 100),
                  Event("seller", "propose", 150),
                  Event("buyer", "reject-proposal", None),
                  Event("seller", "accept-proposal", None)]
        self.assertEqual(simulate(events)[:2], ("deal", 100))
        self.assertEqual(simulate(events, pending_only=True)[:3], ("open", None, 1))

    def test_new_offer_supersedes_old_offer(self):
        events = [Event("buyer", "propose", 100),
                  Event("seller", "propose", 150),
                  Event("buyer", "propose", 125),
                  Event("seller", "accept-proposal", None)]
        self.assertEqual(simulate(events, pending_only=True)[:2], ("deal", 125))

    def test_replay_preserves_all_recorded_baseline_outcomes(self):
        self.assertEqual(len(replay_all()), 36)

    def test_unreadable_message_is_replayed_without_an_act(self):
        events, recorded = parse_log("structured", 25, "termination")["laptop"]
        self.assertEqual(sum(event.act is None for event in events), 1)
        self.assertEqual(simulate(events)[:2], (recorded["outcome"], recorded["price"]))


if __name__ == "__main__":
    unittest.main()
