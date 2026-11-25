"""strategist/publish.py against local bare repositories (no network): the records reach the remote,
unchanged records are not recommitted, a remote that already has commits, moved on, or edited our
files is built upon, an unreachable remote is recorded and caught up later without a second history,
and a lock left by a killed git does not block publishing for good."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-publish-"))
W = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(W))

from strategist import publish, run  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixture_SKILL.md"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], env=ENV, capture_output=True, text=True, check=True).stdout


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="publish-"))
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", str(self.remote))
        shop, home = self.tmp / "shop", self.tmp / "home"
        (shop / "strategy").mkdir(parents=True)
        shutil.copyfile(FIXTURE, shop / "strategy" / "SKILL.md")
        self.cfg = run.Config(shop_home=shop, home=home, log_repo=str(self.remote))
        self.write_run("20261103-1000")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_run(self, rid):
        home = self.cfg.home
        (home / "reports").mkdir(parents=True, exist_ok=True)
        (home / "briefs").mkdir(exist_ok=True)
        (home / "reports" / f"{rid}.md").write_text(f"# {rid}\n", encoding="utf-8")
        (home / "briefs" / f"{rid}.json").write_text("{}", encoding="utf-8")
        with open(home / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": rid}) + "\n")
        return {"run_id": rid, "status": "applied", "mode": "apply", "errors": [],
                "applied": [{"key": "buyer.max_shops", "old": 3, "new": 4}],
                "policy_ver_before": "1/a", "policy_ver_after": "2/b"}

    def remote_log(self):
        return git(self.remote, "log", "--format=%s", "main").splitlines()

    def remote_files(self):
        return set(git(self.remote, "ls-tree", "-r", "--name-only", "main").split())

    def test_disabled_without_a_repository(self):
        self.cfg.log_repo = ""
        self.assertIsNone(publish.publish(self.cfg, {"run_id": "x"}))
        self.assertFalse((self.cfg.home / "logrepo").exists())

    def test_first_publish_reaches_an_empty_remote(self):
        row = publish.publish(self.cfg, self.write_run("20261103-1100"))
        self.assertEqual((row["committed"], row["pushed"], row["error"]), (True, True, None))
        self.assertEqual(self.remote_log(), ["run 20261103-1100: applied; buyer.max_shops 3->4"])
        self.assertTrue({"runs.jsonl", "SKILL.md", "reports/20261103-1000.md", "reports/20261103-1100.md",
                         "briefs/20261103-1100.json"} <= self.remote_files())
        self.assertEqual(git(self.remote, "show", "main:SKILL.md"), FIXTURE.read_text(encoding="utf-8"))

    def test_unchanged_records_are_not_recommitted(self):
        rec = self.write_run("20261103-1100")
        publish.publish(self.cfg, rec)
        # Only the first publish.jsonl row is new: the second commit carries just that file.
        second = publish.publish(self.cfg, rec)
        self.assertEqual((second["committed"], second["pushed"]), (True, True))
        self.assertEqual(git(self.remote, "show", "--format=", "--name-only", "main").split(), ["publish.jsonl"])
        self.assertEqual(len(self.remote_log()), 2)
        rows = [json.loads(x) for x in (self.cfg.home / "publish.jsonl").read_text().splitlines()]
        self.assertEqual([r["pushed"] for r in rows], [True, True])

    def test_builds_on_commits_already_in_the_remote(self):
        seed = self.tmp / "seed"
        git(self.tmp, "clone", "-q", str(self.remote), str(seed))
        (seed / "README.md").write_text("strategist logs\n")
        git(seed, "add", "README.md")
        git(seed, "commit", "-q", "-m", "readme")
        git(seed, "push", "-q", "origin", "HEAD:main")
        row = publish.publish(self.cfg, self.write_run("20261103-1100"))
        self.assertTrue(row["pushed"], row)
        self.assertEqual(self.remote_log(), ["run 20261103-1100: applied; buyer.max_shops 3->4", "readme"])
        self.assertIn("README.md", self.remote_files())

    def test_remote_moved_on_is_rebased_onto(self):
        publish.publish(self.cfg, self.write_run("20261103-1100"))
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", "-b", "main", str(self.remote), str(other))
        (other / "NOTES.md").write_text("by hand\n")
        git(other, "add", "NOTES.md")
        git(other, "commit", "-q", "-m", "notes")
        git(other, "push", "-q", "origin", "HEAD:main")
        row = publish.publish(self.cfg, self.write_run("20261103-1200"))
        self.assertTrue(row["pushed"], row)
        self.assertEqual(self.remote_log()[:2], ["run 20261103-1200: applied; buyer.max_shops 3->4", "notes"])

    def test_unreachable_remote_on_first_publish_starts_no_history(self):
        self.cfg.log_repo = str(self.tmp / "missing.git")
        row = publish.publish(self.cfg, self.write_run("20261103-1100"))
        self.assertEqual((row["committed"], row["pushed"]), (False, False))
        self.assertIn("ls-remote", row["error"])
        self.cfg.log_repo = str(self.remote)
        row = publish.publish(self.cfg, self.write_run("20261103-1200"))
        self.assertTrue(row["pushed"], row)
        self.assertEqual(self.remote_log(), ["run 20261103-1200: applied; buyer.max_shops 3->4"])
        self.assertIn("reports/20261103-1100.md", self.remote_files())   # the earlier run's records too
        published = json.loads(git(self.remote, "show", "main:publish.jsonl").splitlines()[0])
        self.assertFalse(published["pushed"])            # the failed attempt is in the history too

    def test_unreachable_remote_later_keeps_commits_for_the_next_run(self):
        publish.publish(self.cfg, self.write_run("20261103-1100"))
        self.cfg.log_repo = str(self.tmp / "missing.git")
        row = publish.publish(self.cfg, self.write_run("20261103-1200"))
        self.assertEqual((row["committed"], row["pushed"]), (True, False))
        self.cfg.log_repo = str(self.remote)
        row = publish.publish(self.cfg, self.write_run("20261103-1300"))
        self.assertTrue(row["pushed"], row)
        self.assertEqual(len(self.remote_log()), 3)

    def edit_remote(self, name, text):
        other = self.tmp / "other"
        shutil.rmtree(other, ignore_errors=True)
        git(self.tmp, "clone", "-q", "-b", "main", str(self.remote), str(other))
        (other / name).write_text(text)
        git(other, "commit", "-q", "-am", f"edit {name}")
        git(other, "push", "-q", "origin", "HEAD:main")

    def test_conflicting_remote_edit_does_not_stick(self):
        publish.publish(self.cfg, self.write_run("20261103-1100"))
        self.edit_remote("runs.jsonl", "trimmed by hand\n")
        for rid in ("20261103-1200", "20261103-1300"):
            row = publish.publish(self.cfg, self.write_run(rid))
            self.assertTrue(row["pushed"], row)
        ours = (self.cfg.home / "runs.jsonl").read_text()
        self.assertEqual(git(self.remote, "show", "main:runs.jsonl"), ours)    # our records win

    def test_lost_working_copy_while_offline_rejoins_the_remote(self):
        publish.publish(self.cfg, self.write_run("20261103-1100"))
        shutil.rmtree(self.cfg.home / "logrepo")
        self.cfg.log_repo = str(self.tmp / "missing.git")
        row = publish.publish(self.cfg, self.write_run("20261103-1200"))
        self.assertEqual((row["committed"], row["pushed"]), (False, False))
        self.cfg.log_repo = str(self.remote)
        row = publish.publish(self.cfg, self.write_run("20261103-1300"))
        self.assertTrue(row["pushed"], row)
        self.assertEqual(len(self.remote_log()), 2)                  # on top of the first run, one history

    def test_stale_lock_from_a_killed_git_is_cleared(self):
        publish.publish(self.cfg, self.write_run("20261103-1100"))
        lock = self.cfg.home / "logrepo" / ".git" / "index.lock"
        lock.write_text("")
        old = time.time() - publish.STALE_LOCK_S - 60
        os.utime(lock, (old, old))
        row = publish.publish(self.cfg, self.write_run("20261103-1200"))
        self.assertTrue(row["pushed"], row)
        self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
