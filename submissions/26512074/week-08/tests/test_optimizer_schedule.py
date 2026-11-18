import unittest
from datetime import datetime,timezone,timedelta
import optimizer as o

def epoch(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone(timedelta(hours=9))).timestamp()

class ScheduleTests(unittest.TestCase):
 def test_all_four_korean_boundaries(self):
  for hour in (0,6,12,18):
   boundary=epoch(f'2026-11-05T{hour:02d}:00:00')
   self.assertTrue(o.review_due({'last_at':boundary-1},boundary))
   self.assertFalse(o.review_due({'last_at':boundary},boundary+1))
   self.assertEqual(o.next_review_at(boundary-1),boundary)
 def test_manual_run_does_not_delay_next_fixed_time(self):
  manual=epoch('2026-11-04T16:20:00')
  expected=epoch('2026-11-04T18:00:00')
  self.assertEqual(o.next_review_at(manual),expected)
  self.assertTrue(o.review_due({'last_at':manual},expected))
 def test_restart_runs_one_missed_slot_and_legacy_state_migrates(self):
  previous=epoch('2026-11-04T15:23:00')
  self.assertFalse(o.review_due({'last_at':previous},epoch('2026-11-04T17:00:00')))
  self.assertTrue(o.review_due({'last_at':previous},epoch('2026-11-05T07:00:00')))
  self.assertTrue(o.review_due({},previous))

if __name__=='__main__':unittest.main()
