import unittest
from seller_requirements import initial,update,violations
class StructuredPolicyTests(unittest.TestCase):
    def test_explicit_json_policy_binds_grade_cert_lifetime_and_coverage(self):
        p=initial('I want G. Required checkout policy: {"cert":true,"grades":["A","B"],"min_hours_left":360,"warranty_hours":6}')
        self.assertEqual(p['grades'],['A','B']);self.assertEqual(p['min_hours_left'],360);self.assertEqual(p['warranty_hours'],6);self.assertTrue(p['cert_required'])
        self.assertIn('unit does not meet the requested grade',violations({'kind':'goods','grade':'C'},p,100,warranty_hours=6))
    def test_numeric_followup_keeps_policy_and_invalid_json_cannot_erase_conditions(self):
        p=initial('grade A or B; must last 48 hours')
        q=update(p,'1234');self.assertEqual(q['grades'],p['grades']);self.assertEqual(q['min_hours_left'],48)
        q=update(p,'Required checkout policy: {"grades":["Z"],"min_hours_left":true}')
        self.assertEqual(q['grades'],p['grades']);self.assertEqual(q['min_hours_left'],48)
