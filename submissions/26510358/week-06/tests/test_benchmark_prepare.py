"""Pinned preparation tests use in-memory archives and never make network calls."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import benchmark_prepare as prepare


SHA = "a" * 40
ENTRY = {"number": 296, "head_repo_full_name": "example/course", "head_sha": SHA,
         "title": "[week-06] 12345678"}
AGENT = {"harness": "original", "buyer": {"cmd": "python buyer.py {url}"},
         "seller": {"cmd": "python seller.py --port {port}"}}


def archive_bytes(members=None):
    buffer = io.BytesIO()
    prefix = f"course-{SHA}/submissions/12345678/week-06/"
    members = members if members is not None else [
        (prefix + "agent.json", json.dumps(AGENT).encode(), None),
        (prefix + "seller.py", b"original source\n", None),
        (prefix + "logs/old.log", b"historical log", None),
        (prefix + ".env", b"excluded local environment", None),
        (f"course-{SHA}/submissions/other/week-06/other.py", b"do not copy", None)]
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, link in members:
            info = tarfile.TarInfo(name)
            if link:
                info.type, info.linkname = tarfile.SYMTYPE, link
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class BenchmarkPrepareTests(unittest.TestCase):
    def test_manifest_requires_exact_commit_and_safe_repository(self):
        for update in ({"head_sha": "main"}, {"head_repo_full_name": "x/../../evil"},
                       {"student_id": "../escape"}):
            with self.subTest(update=update), self.assertRaises(prepare.PreparationError):
                prepare.validate_entries([{**ENTRY, **update}])
        entry = prepare.validate_entries([ENTRY])[0]
        self.assertEqual(entry["student_id"], "12345678")
        self.assertEqual(entry["archive_url"], f"https://codeload.github.com/example/course/tar.gz/{SHA}")

    def test_extraction_rejects_traversal_links_and_duplicate_files(self):
        prefix = f"course-{SHA}/submissions/12345678/week-06/"
        bad_cases = [[("../escape", b"bad", None)],
                     [(prefix + "link", b"", "/tmp/escape")],
                     [(prefix + "same.py", b"a", None), (prefix + "same.py", b"b", None)]]
        for members in bad_cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "archive.tar.gz"
                path.write_bytes(archive_bytes(members))
                with self.assertRaises(prepare.PreparationError):
                    prepare.extract_submission(path, Path(temporary) / "extract",
                                               prepare.validate_entries([ENTRY])[0])

    def test_prepare_copies_only_submission_hashes_bytes_and_never_executes_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ours = root / "source"
            ours.mkdir()
            (ours / "agent.json").write_text(json.dumps(AGENT))
            (ours / "buyer.py").write_text("source before preparation")
            manifest = root / "source-manifest.json"
            manifest.write_text(json.dumps([ENTRY]))
            calls = []

            def download(url, destination):
                calls.append(url)
                destination.write_bytes(archive_bytes())

            with patch.object(prepare, "git_head", return_value="b" * 40):
                report = prepare.prepare(root / "prepared", ours=ours, source_manifest=manifest,
                                         downloader=download)
            self.assertEqual(report["status"], "ready")
            self.assertFalse(report["agents_executed"])
            self.assertEqual(len(calls), 1)
            participants = json.loads((root / "prepared/participants.json").read_text())
            peer = participants[1]
            self.assertEqual(peer["agent"], AGENT)
            self.assertEqual(Path(peer["path"]).joinpath("seller.py").read_bytes(), b"original source\n")
            self.assertFalse(Path(peer["path"]).joinpath("logs").exists())
            self.assertFalse(Path(peer["path"]).joinpath(".env").exists())
            self.assertEqual(peer["source_hashes"], prepare.file_hashes(peer["path"]))
            Path(participants[0]["path"]).joinpath("buyer.py").write_text("independent copy")
            self.assertEqual((ours / "buyer.py").read_text(), "source before preparation")

    def test_download_failure_is_recorded_without_branch_fallback_or_usable_participants(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ours = root / "source"
            ours.mkdir()
            (ours / "agent.json").write_text(json.dumps(AGENT))
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([ENTRY]))
            with patch.object(prepare, "git_head", return_value=None), \
                    patch.object(prepare.urllib.request, "urlopen", side_effect=OSError("offline")) as request:
                report = prepare.prepare(root / "prepared", ours=ours, source_manifest=manifest)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(request.call_count, 1)
            self.assertFalse((root / "prepared/participants.json").exists())
            self.assertTrue((root / "prepared/pr296-preparation.json").exists())
            self.assertFalse(report["branch_fallback"])

    def test_existing_output_refused_before_any_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ours = root / "source"
            ours.mkdir()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([ENTRY]))
            with patch.object(prepare.urllib.request, "urlopen") as request:
                with self.assertRaises(FileExistsError):
                    prepare.prepare(root, ours=ours, source_manifest=manifest)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
