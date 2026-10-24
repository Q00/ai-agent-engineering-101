"""Repository and shallow container layouts must both parse before startup."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import market_runtime


class RuntimePathTests(unittest.TestCase):
    def test_repository_kit_discovered_from_parent_directories(self):
        private = BASE / '.market'
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as temporary:
            root = Path(temporary)
            kit = root / 'scripts/market_kit.py'
            kit.parent.mkdir()
            kit.write_text('# fixture\n')
            source = root / 'submissions/student/week-07/market_runtime.py'
            self.assertEqual(market_runtime.default_kit_path(source), kit)

    def test_shallow_container_path_uses_container_kit(self):
        container = Path('/opt/market/market_kit.py')
        with patch.object(Path, 'is_file', autospec=True, side_effect=lambda p: p == container):
            self.assertEqual(market_runtime.default_kit_path('/data/market_runtime.py'), container)
            self.assertEqual(market_runtime.default_kit_path('/market_runtime.py'), container)

    def test_explicit_kit_does_not_evaluate_default_discovery(self):
        with patch.object(market_runtime, '__file__', '/data/market_runtime.py'), \
                patch.object(market_runtime, 'default_kit_path', side_effect=AssertionError('must not run')), \
                patch.object(Path, 'is_file', return_value=True):
            args = market_runtime.parse_arguments(['--name', 'fixture', '--kit', '/custom/kit.py'])
        self.assertEqual(args.kit, Path('/custom/kit.py'))

    def test_missing_kit_has_actionable_argument_error(self):
        with patch.object(market_runtime, 'default_kit_path', return_value=None), \
                redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
            market_runtime.parse_arguments(['--name', 'fixture'])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('specify --kit', errors.getvalue())

    def test_help_at_shallow_path_needs_no_kit_or_network(self):
        with patch.object(market_runtime, '__file__', '/data/market_runtime.py'), \
                patch.object(market_runtime, 'default_kit_path', side_effect=AssertionError('must not run')), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            market_runtime.parse_arguments(['--help'])
        self.assertEqual(raised.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
