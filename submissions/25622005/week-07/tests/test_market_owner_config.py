"""Local claim-code handling with synthetic credentials and no network."""
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from configure_market_owner import kit_environment, save_owner


class MarketOwnerConfigTests(unittest.TestCase):
    def setUp(self):
        private = BASE / '.market'
        private.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=private)
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name)

    def test_absent_local_code_preserves_inherited_environment(self):
        self.assertEqual(kit_environment(self.private, {'A': 'B'}), {'A': 'B'})

    def test_saved_code_is_private_and_used_without_mutating_caller_environment(self):
        save_owner(self.private, ' synthetic-claim-code ')
        self.assertEqual((self.private / 'market-owner').stat().st_mode & 0o777, 0o600)
        inherited = {'A': 'B'}
        environment = kit_environment(self.private, inherited)
        self.assertEqual(environment['MARKET_OWNER'], 'synthetic-claim-code')
        self.assertNotIn('MARKET_OWNER', inherited)

    def test_invalid_input_retains_existing_code(self):
        save_owner(self.private, 'synthetic-claim-code')
        with self.assertRaises(ValueError):
            save_owner(self.private, 'bad code')
        self.assertEqual(kit_environment(self.private, {})['MARKET_OWNER'], 'synthetic-claim-code')

    def test_invalid_local_file_error_does_not_disclose_contents(self):
        (self.private / 'market-owner').write_text('sensitive bad value')
        with self.assertRaises(ValueError) as caught:
            kit_environment(self.private, {})
        self.assertNotIn('sensitive', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
