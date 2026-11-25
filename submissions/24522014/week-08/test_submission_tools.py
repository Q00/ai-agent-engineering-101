from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import log_manifest as lm
import scan_unpushed as scan

FAKE = "sk-" + "abc123DEF456ghi789JKL012mno"
LOCAL = "local" + "-secret-value-9876543210"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd, capture_output=True, check=True).stdout.decode()


class ScanLineTests(unittest.TestCase):
    def kinds(self, line, secrets=None):
        secrets = secrets or {}
        return scan.scan_line(line, scan.compile_secrets(secrets), secrets)

    def test_shapes_are_found_and_lookalikes_are_not(self):
        self.assertIn("shape:openai_or_anthropic_key", self.kinds(FAKE.encode()))
        self.assertEqual(self.kinds(b"task-" + b"a" * 30 + b" risk-" + b"b" * 30), [])
        self.assertIn("shape:bearer_value", self.kinds(b'Authorization: Bearer ' + b"x" * 30))
        self.assertIn("shape:auth_header", self.kinds(b'"authorization": "' + b"x" * 30 + b'"'))
        self.assertIn("shape:private_key_block", self.kinds(("-----BEGIN " + "PRIVATE KEY-----").encode()))
        self.assertIn("shape:github_token", self.kinds(("gh" + "p_" + "a" * 30).encode()))
        self.assertIn("shape:google_key", self.kinds(("AI" + "za" + "a" * 35).encode()))
        self.assertIn("shape:env_assignment", self.kinds(("OPENAI_" + "API_KEY=abcdefgh12345").encode()))

    def test_code_that_reads_a_secret_is_only_a_mention(self):
        kinds = self.kinds(b'headers={"Authorization": f"Bearer {token}"}')
        self.assertFalse([k for k in kinds if k.startswith("shape:")])
        self.assertIn("mention:bearer", kinds)
        self.assertFalse([k for k in self.kinds(b"--owner CODE  claim code from the join page") if k.startswith("shape:")])

    def test_utf16_lines_are_matched(self):
        self.assertIn("shape:openai_or_anthropic_key", self.kinds(FAKE.encode("utf-16-le")))
        secrets = {LOCAL.encode(): "dotenv:X"}
        self.assertIn("exact:dotenv:X", self.kinds(LOCAL.encode("utf-16-le"), secrets))

    def test_masked_context_has_no_value(self):
        views = scan.masked(f"before {FAKE} after".encode())
        self.assertTrue(views)
        self.assertNotIn(FAKE, " ".join(views))


class ScanRepoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "week"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "core.autocrlf", "false")

    def commit(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text)
        git(self.root, "add", "-f", name)
        git(self.root, "commit", "-q", "-m", "c")
        return git(self.root, "rev-parse", "HEAD").strip()

    def test_added_lines_only_with_right_line_numbers_and_no_value_in_output(self):
        first = self.commit("a.txt", b"one\n" + FAKE.encode() + b"\nthree\n")
        second = self.commit("a.txt", b"one\nfixed\nthree\n" + LOCAL.encode() + b"\n")
        secrets = {LOCAL.encode(): "dotenv:X"}
        hits, stats, _ = scan.scan_commits([first, second], self.root, scan.compile_secrets(secrets), secrets)
        found = {(h["commit"], h["file"], h["line"], h["kind"]) for h in hits}
        self.assertIn((first[:12], "a.txt", 2, "shape:openai_or_anthropic_key"), found)
        self.assertIn((second[:12], "a.txt", 4, "exact:dotenv:X"), found)
        self.assertEqual(stats["commits"], 2)
        self.assertEqual(stats["added_lines"], 5)
        text = json.dumps(hits)
        self.assertNotIn(FAKE, text)
        self.assertNotIn(LOCAL, text)

    def test_a_line_that_looks_like_a_diff_header_is_still_content(self):
        sha = self.commit("a.txt", b"++ b/other\n" + FAKE.encode() + b"\n")
        hits, _, _ = scan.scan_commits([sha], self.root, None, {})
        self.assertEqual({(h["file"], h["line"]) for h in hits}, {("a.txt", 2)})

    def test_secret_file_names_are_reported(self):
        sha = self.commit("sub/shop_key.pem", b"x\n")
        hits, _, _ = scan.scan_commits([sha], self.root, None, {})
        self.assertIn("secret_file_name", {h["kind"] for h in hits})

    def test_local_secrets_are_loaded_from_env_market_and_environment(self):
        (self.root / ".env").write_text("NAME=" + LOCAL + "\n", encoding="utf-8")
        (self.root / ".market").mkdir()
        (self.root / ".market" / "s.json").write_text(json.dumps({"auth_token": "token-value-1234567", "message_key": "public-value-1234567"}), encoding="utf-8")
        found = scan.load_secrets(self.root, {"MY_API_KEY": "environment-value-123", "PATH": "not-a-secret-value-123"})
        self.assertEqual(found[LOCAL.encode()], "dotenv:NAME")
        self.assertEqual(found[b"token-value-1234567"], "market:s.json")
        self.assertEqual(found[b"environment-value-123"], "environment:MY_API_KEY")
        self.assertNotIn(b"public-value-1234567", found)
        self.assertNotIn(b"not-a-secret-value-123", found)


class ManifestTests(unittest.TestCase):
    CUTOFF = lm.ROUND_START + timedelta(days=2)

    def test_classify_covers_each_path(self):
        model = {"outcome": "purchased", "decision_trail": [{"source": "deterministic"}, {"source": "model"}]}
        self.assertEqual(lm.classify_buyer(model), ["model_path_success"])
        self.assertEqual(lm.classify_buyer({"outcome": "purchased", "decision_trail": [{"source": "model_gate"}]}), ["rule_path_success"])
        self.assertEqual(lm.classify_buyer({"outcome": "purchased"}), ["rule_path_success"])
        self.assertEqual(lm.classify_buyer({"outcome": "no_deal", "negotiation_outcome": "seller_rejected"}), ["seller_rejected"])
        self.assertEqual(lm.classify_buyer({"outcome": "receipt_mismatch"}), ["receipt_mismatch"])
        self.assertEqual(lm.classify_buyer({"outcome": "transport_error"}), ["failed_attempt"])
        self.assertEqual(lm.classify_buyer({"outcome": "research_skipped"}), [])

    def test_windows(self):
        self.assertEqual(lm.window_of(lm.ROUND_START + timedelta(hours=1), self.CUTOFF), "early")
        self.assertEqual(lm.window_of(self.CUTOFF - timedelta(hours=1), self.CUTOFF), "recent")
        self.assertEqual(lm.window_of(lm.ROUND_START + timedelta(hours=20), self.CUTOFF), "middle")
        self.assertIsNone(lm.window_of(lm.ROUND_START - timedelta(hours=1), self.CUTOFF))
        self.assertIsNone(lm.window_of(self.CUTOFF + timedelta(hours=1), self.CUTOFF))

    def test_pick_is_repeatable_independent_of_order_and_takes_both_windows(self):
        rows = [(f"n{i}", "early") for i in range(20)] + [(f"m{i}", "recent") for i in range(20)] + [(f"z{i}", "middle") for i in range(20)]
        one = lm.pick({"k": rows})
        two = lm.pick({"k": list(reversed(rows))})
        self.assertEqual(sorted(one), sorted(two))
        self.assertEqual(sum(w == "early" for _, w, _ in one), 5)
        self.assertEqual(sum(w == "recent" for _, w, _ in one), 5)
        self.assertNotEqual(sorted(n for _, _, n in lm.pick({"k": rows}, seed="other")), sorted(n for _, _, n in one))

    def test_an_empty_window_falls_back_to_the_round_and_says_so(self):
        rows = [(f"n{i}", "middle") for i in range(7)] + [("e", "early")]
        chosen = lm.pick({"k": rows})
        self.assertEqual({w for _, w, _ in chosen}, {"early", "whole_round"})
        names = [n for _, _, n in chosen]
        self.assertEqual(len(names), len(set(names)))

    def test_inventory_and_samples_on_a_small_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            start = lm.ROUND_START + timedelta(hours=1)
            stamp = start.strftime("%Y%m%dT%H%M%S") + "000000Z"
            for sub, name, data in (("market-buyer", f"{stamp}-a.json", {"record": {"outcome": "purchased", "buyer_log_dir": f"C:\\x\\logs\\buyer\\{stamp}-b"}}),
                                    ("seller", f"{stamp}-c.json", {"phase": "opening"}), ("pricing", "abc.json", {"floor": 1})):
                (root / "logs" / sub).mkdir(parents=True, exist_ok=True)
                (root / "logs" / sub / name).write_text(json.dumps(data), encoding="utf-8")
            (root / "logs" / "buyer" / f"{stamp}-b").mkdir(parents=True)
            (root / "logs" / "buyer" / f"{stamp}-b" / "outcome.json").write_text("{}", encoding="utf-8")
            (root / "logs" / "harness" / f"{stamp}-d-codex").mkdir(parents=True)
            (root / "logs" / "harness" / f"{stamp}-d-codex" / "result.json").write_text("{}", encoding="utf-8")
            (root / "logs" / "top.log").write_text("x", encoding="utf-8")
            os.utime(root / "logs" / "pricing" / "abc.json", (start.timestamp(), start.timestamp()))
            folders = lm.merged_top_files(lm.inventory(root / "logs", pause=0))
            self.assertEqual(folders["market-buyer"]["files"], 1)
            self.assertEqual(folders["(top-level files)"]["files"], 1)
            again = lm.merged_top_files(lm.inventory(root / "logs", pause=0))
            self.assertEqual(lm.overall_list_sha256(folders), lm.overall_list_sha256(again))
            candidates, links, held = lm.collect(root, self.CUTOFF, pause=0)
            self.assertEqual(len(candidates["rule_path_success"]), 1)
            self.assertEqual(held["pricing"], [("abc.json", "early")])
            rows, paths = lm.sample_paths(root, lm.pick(candidates), links)
            self.assertIn(f"logs/buyer/{stamp}-b/outcome.json", paths)
            self.assertIn(f"logs/harness/{stamp}-d-codex/result.json", paths)
            self.assertFalse([p for p in paths if "pricing" in p])
            self.assertEqual({r["kind"] for r in rows}, {"rule_path_success", "seller_decision", "harness_codex"})

    def test_pricing_is_never_a_sampled_kind(self):
        self.assertNotIn("pricing", lm.RULES["kinds"])
        self.assertIn("pricing", lm.HELD)


if __name__ == "__main__":
    unittest.main()
