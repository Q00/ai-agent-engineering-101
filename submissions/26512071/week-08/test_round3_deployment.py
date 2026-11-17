"""Only whitelist launcher env edits; never log/copy credential-bearing text."""
import unittest
try:
    from round3_deployment import neutral_launcher
except ModuleNotFoundError:
    neutral_launcher = None

class Deployment(unittest.TestCase):
    def test_only_authorized_env_lines_change_secret_and_commands_untouched(self):
        self.assertIsNotNone(neutral_launcher, 'safe launcher edit feature missing')
        source='$env:MARKET_OWNER = "synthetic-fixture-not-a-credential"\r\n$env:BUYER_ALLIES = "friend" # unverified approval\r\n$env:SHOP_ALLIES = "friend"\r\n$env:BUYER_EXPLORE = "0.25"\r\nStart-Process existing-command\r\n'
        result=neutral_launcher(source)
        self.assertIn('$env:MARKET_OWNER = "synthetic-fixture-not-a-credential"\r\n', result)
        self.assertIn('$env:BUYER_ALLIES = ""\r\n', result)
        self.assertIn('$env:SHOP_ALLIES = ""\r\n', result)
        self.assertIn('$env:SHOP_POLICY = "adaptive"\r\n', result)
        self.assertIn('$env:BUYER_ALLY_RATE = "0"\r\n', result)
        self.assertIn('$env:BUYER_EXPLORE = "0.25"\r\nStart-Process existing-command\r\n',result)
        self.assertNotIn('unverified approval', result)
        self.assertEqual(neutral_launcher(result),result)

if __name__=='__main__': unittest.main()
