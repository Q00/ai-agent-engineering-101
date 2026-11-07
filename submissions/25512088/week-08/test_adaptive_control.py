import sqlite3
from pathlib import Path
import tempfile
import unittest

from adaptive_control import Control, stable_arm


class AdaptiveControlTests(unittest.TestCase):
    def test_assignment_is_deterministic_and_bounded(self):
        self.assertEqual(stable_arm("e", "u"), stable_arm("e", "u"))
        arms={stable_arm("e",str(i))[0] for i in range(200)}
        self.assertEqual(arms,{"treatment","control"})
        with self.assertRaises(ValueError):stable_arm("", "u")

    def test_events_are_append_only_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Control(tmp)
            c.event("buyer","decision",{"member_key":"SECRET","count":2})
            row=c.db.execute("SELECT body FROM runtime_events").fetchone()[0]
            self.assertNotIn("SECRET",row)
            with self.assertRaises(sqlite3.DatabaseError):
                with c.db:c.db.execute("UPDATE runtime_events SET kind='x'")
            c.close()

    def test_mutation_idempotency_and_legal_transitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Control(tmp)
            first,created=c.prepare_mutation("buyer","offer","order:1",{"amount":10})
            second,created_again=c.prepare_mutation("buyer","offer","order:1")
            self.assertTrue(created);self.assertFalse(created_again);self.assertEqual(first,second)
            c.move_mutation(first,"SUBMITTED","sent")
            c.move_mutation(first,"UNCERTAIN","timeout")
            c.move_mutation(first,"RECONCILING","poll")
            c.move_mutation(first,"PRESERVED","unresolved")
            with self.assertRaises(ValueError):c.move_mutation(first,"CONFIRMED","illegal")
            self.assertEqual(len(c.unresolved("buyer")),1)
            c.close()

    def test_freshness_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Control(tmp)
            c.save_input("board",{"round":3},fetched=100)
            self.assertTrue(c.input_fresh("board",90,now=190))
            self.assertFalse(c.input_fresh("board",90,now=191))
            cp=c.checkpoint("seller",{"price":1})
            self.assertTrue(cp)
            c.close()

    def test_reconciliation_spacing_limit_and_official_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Control(tmp);mutation,_=c.prepare_mutation('buyer','offer','reconcile')
            c.move_mutation(mutation,'SUBMITTED','sent');c.move_mutation(mutation,'UNCERTAIN','timeout')
            self.assertEqual(c.reconcile_mutation(mutation,'unknown',now=100),'RECONCILING')
            self.assertEqual(c.reconcile_mutation(mutation,'unknown',now=101),'WAIT')
            for index in range(1,20):c.reconcile_mutation(mutation,'unknown',now=100+index*30)
            self.assertEqual(c.mutation_state(mutation),'PRESERVED')
            self.assertEqual(c.reconcile_mutation(mutation,'unknown',now=1000),'PRESERVED')
            self.assertEqual(c.reconcile_mutation(mutation,'confirmed','ledger:42',now=1000),'CONFIRMED')
            c.close()


if __name__ == "__main__":unittest.main()
