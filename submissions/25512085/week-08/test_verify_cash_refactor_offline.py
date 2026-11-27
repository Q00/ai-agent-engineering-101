"""Temporary-parent selection only; no files, network or market execution."""
from pathlib import Path
import unittest
from unittest.mock import patch

import verify_cash_refactor_offline as verify


class TempParentTests(unittest.TestCase):
    def test_approved_per_user_parent_is_preferred(self):
        with patch.dict(verify.os.environ, {}, clear=True), \
                patch.object(Path, 'is_dir', return_value=True), \
                patch.object(verify.tempfile, 'gettempdir') as fallback:
            self.assertEqual(verify.select_temp_parent(), verify.PREFERRED_TEMP.resolve())
        fallback.assert_not_called()

    def test_missing_preferred_parent_uses_platform_default(self):
        fallback = Path.home() / 'portable-test-temp'
        with patch.dict(verify.os.environ, {}, clear=True), \
                patch.object(Path, 'is_dir', side_effect=[False, True]), \
                patch.object(verify.tempfile, 'gettempdir', return_value=str(fallback)):
            self.assertEqual(verify.select_temp_parent(), fallback.resolve())

    def test_explicit_environment_override(self):
        parent = Path.home() / 'explicit-test-temp'
        with patch.dict(verify.os.environ, {'OPENCODE_TEMP_DIR':str(parent)}), \
                patch.object(Path, 'is_dir', return_value=True):
            self.assertEqual(verify.select_temp_parent(), parent.resolve())

    def test_missing_override_is_rejected_without_creation(self):
        with patch.dict(verify.os.environ, {'OPENCODE_TEMP_DIR':str(Path.home() / 'missing-temp')}), \
                patch.object(Path, 'is_dir', return_value=False):
            with self.assertRaises(ValueError):
                verify.select_temp_parent()

    def test_repository_override_is_rejected(self):
        with patch.dict(verify.os.environ, {'OPENCODE_TEMP_DIR':str(verify.BASE)}):
            with self.assertRaises(ValueError):
                verify.select_temp_parent()


if __name__ == '__main__':
    unittest.main()
