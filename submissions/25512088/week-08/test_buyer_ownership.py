import unittest
from buyer_r3 import verified_other_shops

class OwnershipTests(unittest.TestCase):
    def test_same_owner_alias_and_unknown_are_excluded(self):
        shops=[{'name': n} for n in ['own','alias','other','house','unknown','missing']]
        board={'shops': [
            {'name':'own','owner':{'login':'Me'}},
            {'name':'alias','owner':{'login':'me'}},
            {'name':'other','owner':{'login':'Them'}},
            {'name':'house','house':True},
            {'name':'unknown','owner':None}]}
        self.assertEqual(verified_other_shops(shops,board,'own'),[{'name':'other'},{'name':'house'}])

    def test_missing_own_identity_fails_closed(self):
        for board in ({}, {'shops':[{'name':'own','owner':None}]}):
            with self.assertRaises(ValueError):
                verified_other_shops([{'name':'other'}],board,'own')
