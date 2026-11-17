import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import storage_audit
from storage import RedisDeals, RedisTaskStore, Task, TaskVersion


class AuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / "logs")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.stderr = io.StringIO()
        for name, value in [("LOG_DIR", self.directory), ("sys.stderr", self.stderr)]:
            p = patch("storage_audit." + name, value)
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        return [json.loads(line) for p in self.directory.glob("*.jsonl")
                for line in p.read_text().splitlines()]

    async def test_save_read_miss_delete_and_redaction(self):
        client = AsyncMock()
        store = RedisDeals(client)
        await store.save("private-task", {"secret": "private-content"})
        client.get.return_value = '{"secret":"private-content"}'
        self.assertEqual(await store.get("private-task"), {"secret": "private-content"})
        client.get.return_value = None
        self.assertIsNone(await store.get("private-task"))
        await store.delete("private-task")
        rows = self.rows()
        self.assertEqual([r["operation"] for r in rows], ["save", "get", "get", "delete"])
        self.assertEqual([r["found"] for r in rows if "found" in r], [True, False])
        self.assertEqual(len({r["task_ref"] for r in rows}), 1)
        self.assertNotIn("private-", json.dumps(rows) + self.stderr.getvalue())

    async def test_failure_is_logged_without_exception_secret_and_reraised(self):
        client = AsyncMock()
        failure = ConnectionError("redis://private-password@example")
        client.set.side_effect = failure
        with self.assertRaises(ConnectionError) as caught:
            await RedisDeals(client).save("private-task", {})
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.rows()[0]["outcome"], "error")
        self.assertNotIn("private-password", self.stderr.getvalue())

    async def test_unwritable_log_does_not_fail_successful_write(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError):
            await RedisDeals(AsyncMock()).save("task", {})
        self.assertIn("redis_audit_file_unavailable", self.stderr.getvalue())

    async def test_task_save_preserves_version_and_get_reads_serialized_state(self):
        client = MagicMock()
        pipe = MagicMock()
        pipe.watch = AsyncMock()
        pipe.hgetall = AsyncMock(return_value={})
        pipe.execute = AsyncMock(return_value=[1, True])
        client.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipe)
        client.pipeline.return_value.__aexit__ = AsyncMock(return_value=False)
        store = RedisTaskStore(client)
        task = Task(id="private-task")
        with patch.object(store, "_prefix", return_value="test:"):
            version = await store.save(task, prev_version=TaskVersion(0), context=None)
            self.assertEqual(version, TaskVersion(1))
            client.hgetall = AsyncMock(return_value={
                b"task": task.SerializeToString(), b"version": b"1"})
            restored = await store.get(task.id, context=None)
        self.assertEqual(restored.task.id, task.id)
        self.assertEqual(self.rows()[0]["version"], "TaskVersion(1)")
        self.assertTrue(self.rows()[1]["found"])


if __name__ == "__main__":
    unittest.main()
