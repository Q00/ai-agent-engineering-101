"""Regression: registry nickname is not necessarily signed receipt display name."""
import json,unittest
from pathlib import Path
import buyer_r3
class RegistryNicknameRegression(unittest.TestCase):
 def test_registry_pubkey_authenticates_different_signed_display_name(self):
  f=json.loads((Path(__file__).parent/'fixture_authentic_receipt_20261112.json').read_text(encoding='utf-8'))
  r=f['receipt']
  # Same URL/key/task/SKU/price, different registry nickname. Registry nickname is NOT an authenticated receipt alias contract.
  out=buyer_r3.valid_receipt(r,f['pubkey'],r['item'],r['task'],r['price'],r['price'],{},shop_name='25620027',shop_url=f['shop_url'])
  self.assertEqual(out,r)
if __name__=='__main__':unittest.main()
