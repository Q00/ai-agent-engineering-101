import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storage_env import load_storage_environment


class StorageEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / ".git").mkdir()
        self.root = self.repo / "submissions" / "student" / "week-08"
        self.root.mkdir(parents=True)

    def test_repository_env_loaded_without_exposing_other_settings(self):
        (self.repo / ".env").write_text(
            'export REDIS_URL="redis://user:p${LITERAL}@localhost:6379/2"\n'
            'TASK_TTL_SECONDS=3600\nUNRELATED_SECRET=do-not-load\n')
        with patch.dict(os.environ, {}, clear=True):
            load_storage_environment(self.root)
            self.assertEqual(os.environ["REDIS_URL"], "redis://user:p${LITERAL}@localhost:6379/2")
            self.assertEqual(os.environ["TASK_TTL_SECONDS"], "3600")
            self.assertNotIn("UNRELATED_SECRET", os.environ)

    def test_environment_then_project_then_repository_precedence(self):
        (self.repo / ".env").write_text('REDIS_URL=redis://repository\nTASK_TTL_SECONDS=900\n')
        (self.root / ".env").write_text('REDIS_URL=redis://project\n')
        with patch.dict(os.environ, {}, clear=True):
            load_storage_environment(self.root)
            self.assertEqual(os.environ["REDIS_URL"], "redis://project")
            self.assertEqual(os.environ["TASK_TTL_SECONDS"], "900")
        for existing in ("redis://deployment", ""):
            with patch.dict(os.environ, {"REDIS_URL": existing}, clear=True):
                load_storage_environment(self.root)
                self.assertEqual(os.environ["REDIS_URL"], existing)

    def test_missing_env_preserves_memory_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            load_storage_environment(self.root)
            self.assertNotIn("REDIS_URL", os.environ)

    def test_no_repository_marker_does_not_load_unrelated_parent_env(self):
        (self.repo / ".git").rmdir()
        (self.repo / ".env").write_text('REDIS_URL=redis://unrelated\n')
        (self.root / ".env").write_text('TASK_TTL_SECONDS=600\n')
        with patch.dict(os.environ, {}, clear=True):
            load_storage_environment(self.root)
            self.assertNotIn("REDIS_URL", os.environ)
            self.assertEqual(os.environ["TASK_TTL_SECONDS"], "600")


if __name__ == "__main__":
    unittest.main()
