"""Offline deployment checks: no SSH, model calls, registrations or live service."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deploy.deploy_v4 import prepare
from deploy import install_server as server

ROOT = Path(__file__).resolve().parent


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def fixture(self):
        source = self.folder / "source"
        source.mkdir()
        for name, text in {"agent.json": "{}", "advisor-config.json": "{}", "shop-adaptive.json": '{"name": "dyishappy"}',
                           "seller_r3.py": "# public source\n",
                           ".gitignore": "*.pem\n", "prompts/seller.txt": "sample prompt",
                           "deploy/install_server.py": "# public installer\n",
                           "vendor/market_kit_v4.py": "# public kit\n", "vendor/SOURCE.json": "{}",
                           "shop_key.pem": "DO NOT COPY PRIVATE KEY",
                           "buyer_key.pem": "DO NOT COPY PRIVATE KEY", ".env": "DO NOT COPY TOKEN",
                           "credentials.json": "DO NOT COPY TOKEN", ".market/memory.json": "PRIVATE MEMORY",
                           "logs/history.log": "ORIGINAL LOG", ".venv/private.py": "NOT SOURCE"}.items():
            path = source / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
        with redirect_stdout(io.StringIO()):
            run, archive, digest, evidence = prepare(source)
        return source, run, archive, digest, evidence

    def test_archive_round_trip_excludes_keys_auth_memory_logs_and_venv(self):
        source, run, archive, digest, evidence = self.fixture()
        stage = self.folder / "stage"
        manifest = server.unpack(archive, digest, stage)
        self.assertEqual(manifest["run"], run)
        self.assertEqual(set(manifest["files"]), {"agent.json", "advisor-config.json", "shop-adaptive.json", "seller_r3.py", ".gitignore",
            "prompts/seller.txt", "deploy/install_server.py", "vendor/market_kit_v4.py", "vendor/SOURCE.json"})
        for name in manifest["files"]:
            self.assertEqual((source / name).read_bytes(), (stage / name).read_bytes())
        self.assertEqual(json.loads((evidence / "package-manifest.json").read_text())["archive_sha256"], digest)

    def test_tampered_transport_archive_is_rejected_before_extracting(self):
        _, _, archive, _, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "checksum"):
            server.unpack(archive, "0" * 64, self.folder / "stage")
        self.assertFalse((self.folder / "stage").exists())

    def test_archive_paths_links_and_private_files_are_rejected(self):
        for i, name in enumerate(("../seller.py", "/seller.py", "shop_key.pem", ".env", "credentials.json",
                                  ".market/inventory.json", "logs/market-kit.log", "seller_r3.py")):
            with self.subTest(name=name):
                archive = self.folder / f"bad-{i}.tar.gz"
                with tarfile.open(archive, "w:gz") as out:
                    member = tarfile.TarInfo(name)
                    if name == "seller_r3.py":
                        member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    out.addfile(member)
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):
                    server.unpack(archive, digest, self.folder / f"stage-{i}")

    def test_file_hash_mismatch_is_rejected_even_with_valid_archive_hash(self):
        archive = self.folder / "mismatch.tar.gz"
        data = {"seller.py": b"changed", "release-manifest.json": json.dumps(
            {"files": {"seller.py": "0" * 64}}).encode()}
        with tarfile.open(archive, "w:gz") as out:
            for name, raw in data.items():
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                out.addfile(member, io.BytesIO(raw))
        with self.assertRaisesRegex(ValueError, "Source checksum"):
            server.unpack(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), self.folder / "stage")

    def test_install_then_rollback_restores_source_and_preserves_live_data(self):
        _, _, archive, digest, _ = self.fixture()
        stage = self.folder / "stage"
        manifest = server.unpack(archive, digest, stage)
        root, backup = self.folder / "live", self.folder / "backup"
        root.mkdir()
        backup.mkdir()
        originals = {"seller_r3.py": b"OLD SOURCE", "shop_key.pem": b"OLD KEY",
                     "buyer_key.pem": b"OLD BUYER KEY", "logs/market-kit.log": b"OLD LOG",
                     ".market/inventory.json": b"PRIVATE LIVE STATE"}
        for name, raw in originals.items():
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
        state = {"files": [], "dropin_existed": False, "was_active": True}
        server.install_sources(root, stage, manifest, backup, state)
        self.assertNotEqual((root / "seller_r3.py").read_bytes(), originals["seller_r3.py"])
        dropin = self.folder / "90-week08.conf"
        dropin.write_text("NEW SERVICE")
        with patch.object(server, "systemctl") as calls, redirect_stdout(io.StringIO()):
            server.rollback(root, backup, dropin, state)
        self.assertFalse(dropin.exists())
        self.assertFalse((root / "agent.json").exists())
        for name, raw in originals.items():
            self.assertEqual((root / name).read_bytes(), raw)
        self.assertTrue(json.loads((backup / "state.json").read_text())["rolled_back"])
        self.assertIn(unittest.mock.call("start", server.UNIT), calls.call_args_list)

    def test_rollback_restores_previous_dropin_and_leaves_stopped_service_stopped(self):
        root, backup = self.folder / "live", self.folder / "backup"
        root.mkdir()
        backup.mkdir()
        dropin = self.folder / "90-week08.conf"
        dropin.write_text("NEW CONFIG")
        (backup / "previous-dropin.conf").write_text("OLD CONFIG")
        state = {"files": [], "dropin_existed": True, "was_active": False}
        with patch.object(server, "systemctl") as calls, redirect_stdout(io.StringIO()):
            server.rollback(root, backup, dropin, state)
        self.assertEqual(dropin.read_text(), "OLD CONFIG")
        self.assertNotIn(unittest.mock.call("start", server.UNIT), calls.call_args_list)

    def test_destination_symlink_cannot_overwrite_private_file(self):
        outside = self.folder / "private.pem"
        outside.write_text("KEEP")
        root = self.folder / "live"
        root.mkdir()
        (root / "seller.py").symlink_to(outside)
        with self.assertRaises(ValueError):
            server.checked_path(root, "seller.py")
        self.assertEqual(outside.read_text(), "KEEP")

    def test_concurrent_deployment_is_rejected(self):
        with server.deployment_lock(self.folder):
            with self.assertRaisesRegex(RuntimeError, "Another deployment"):
                with server.deployment_lock(self.folder):
                    self.fail("Second deployment was allowed")

    def test_only_new_logs_and_latest_start_can_prove_readiness(self):
        path = self.folder / "market-kit.log"
        public = "https://sample.trycloudflare.com"
        good = (f"[kit] market_kit 4, {self.folder}\n"
                f"[shop] your seller on port 1; others reach it through the kit at {public}\n"
                "[market] shop registered as dyishappy, round 3\n"
                "[market] buyer registered as dyishappy, round 3\n")
        path.write_text(good)
        before = server.log_start(path)
        self.assertFalse(server.registrations(server.new_log(path, before), self.folder, "dyishappy")[0])
        with path.open("a") as out:
            out.write(good)
        self.assertEqual(server.registrations(server.new_log(path, before), self.folder, "dyishappy"), (True, public))
        with path.open("a") as out:
            out.write(f"[kit] market_kit 4, {self.folder}\n")
        self.assertFalse(server.registrations(server.new_log(path, before), self.folder, "dyishappy")[0])
        path.replace(self.folder / "market-kit.log.1")
        path.write_text(good)
        self.assertTrue(server.registrations(server.new_log(path, before), self.folder, "dyishappy")[0])

    def test_acp_requires_matching_endpoint_and_checkout_tools(self):
        public = "https://sample.trycloudflare.com"
        card = {"protocol": "acp", "endpoint": public + "/acp",
                "capabilities": {"services": ["checkout"], "payment_methods": ["ap2-mandate"]},
                "tools": [{"name": "create_checkout_session"}, {"name": "complete_checkout_session"}]}
        self.assertTrue(server.acp_ok(card, public))
        self.assertFalse(server.acp_ok(card, "https://other.trycloudflare.com"))
        self.assertFalse(server.acp_ok({**card, "tools": []}, public))
        self.assertFalse(server.acp_ok({**card, "capabilities": None}, public))
        self.assertFalse(server.acp_ok({**card, "tools": [{"name": ["invalid"]}]}, public))

    def test_dropin_uses_week08_but_keeps_existing_auth_and_interpreter(self):
        root, python = Path("/home/student/week08"), Path("/home/student/week07/.venv/bin/python")
        result = server.dropin_text(root, python)
        self.assertIn('ExecStart=\nExecStart="/home/student/week07/.venv/bin/python"', result)
        self.assertIn('"/home/student/week08/run_round3.py" --join', result)
        self.assertNotIn("EnvironmentFile", result)
        self.assertNotIn("MARKET_OWNER", result)
        with self.assertRaises(ValueError):
            server.dropin_text(Path('/home/"invalid'), python)

    def test_working_directory_is_an_absolute_path_without_shell_quotes(self):
        root = Path("/home/student/agent-market-week07/repo/submissions/26510124/week-08")
        config = server.dropin_text(root, Path("/home/student/week07/.venv/bin/python"))
        value = next(line.partition("=")[2] for line in config.splitlines()
                     if line.startswith("WorkingDirectory="))
        # The real systemd parser validates the whole path without shell unquoting.
        self.assertTrue(Path(value).is_absolute())
        self.assertEqual(value, str(root))

    def test_configuration_failure_records_actual_fields_without_secret_args(self):
        for reason in ("directory", "load", "command"):
            with self.subTest(reason=reason):
                evidence = self.folder / reason
                evidence.mkdir()
                root = Path("/home/student/week08")
                fields = {"LoadState": "bad-setting" if reason == "load" else "loaded",
                          "WorkingDirectory": "/home/student/week07" if reason == "directory" else str(root),
                          "FragmentPath": "/home/student/market.service",
                          "DropInPaths": "/home/student/market.service.d/90-week08.conf",
                          "ExecStart": ("/old/runner.py" if reason == "command" else str(root / "run_round3.py"))
                                       + " --owner secret-sentinel"}
                def fake_systemctl(*args, **kwargs):
                    name = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--property="))
                    return subprocess.CompletedProcess(args, 0, stdout=fields[name])
                console = io.StringIO()
                with patch.object(server, "systemctl", side_effect=fake_systemctl), redirect_stdout(console):
                    with self.assertRaisesRegex(ValueError, "Service configuration check failed"):
                        server.verify_service_configuration(root, evidence)
                report = (evidence / "service-configuration.json").read_text()
                self.assertNotIn("secret-sentinel", report + console.getvalue())
                self.assertNotIn("Another systemd override", console.getvalue())
                self.assertEqual(json.loads(report)["WorkingDirectory"], fields["WorkingDirectory"])

    def simulate_deployment(self, *, fail_preflight=False, fail_startup=False):
        _, run, archive, digest, _ = self.fixture()
        fake_home = self.folder / "home"
        root = fake_home / server.RELATIVE
        incoming = root / ".market/incoming" / run
        incoming.mkdir(parents=True)
        (root / "seller_r3.py").write_text("PREVIOUS SOURCE")
        (root / "shop_key.pem").write_text("PRESERVE EXISTING KEY")
        python = root.parent / "week-07/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("TEST STUB; NEVER EXECUTED")
        env_path = fake_home / "agent-market-week07/secrets/market.env"
        env_path.parent.mkdir(parents=True)
        env_path.write_text("MARKET_OWNER=synthetic-test-value\n")
        dropin = fake_home / (".config/systemd/user/" + server.UNIT + ".d/90-week08.conf")
        dropin.parent.mkdir(parents=True)
        dropin.write_text("PREVIOUS SERVICE CONFIG")
        checked_by_systemd = False

        def fake_capture(command, log, env):
            nonlocal checked_by_systemd
            if fail_preflight:
                raise RuntimeError("preflight failed")
            if command[0] == "systemd-analyze":
                self.assertEqual(command[1:3], ["--user", "verify"])
                self.assertTrue(Path(command[-1]).is_file())
                checked_by_systemd = True

        def fake_systemctl(*args, **kwargs):
            if args[0] == "stop":
                self.assertTrue(checked_by_systemd, "Native syntax verification must precede service stop")
            outputs = {"--property=LoadState": "loaded", "--property=EnvironmentFiles": str(env_path),
                       "--property=ExecStart": str(root / "run_round3.py"),
                       "--property=WorkingDirectory": str(root)}
            value = "active" if args[0] == "is-active" else ""
            for flag, text in outputs.items():
                if flag in args:
                    value = text
            return subprocess.CompletedProcess(args, 0, stdout=value, stderr="")

        with patch.object(server.Path, "home", return_value=fake_home), \
             patch.object(server.shutil, "which", return_value="/synthetic/cli"), \
             patch.object(server, "systemctl", side_effect=fake_systemctl) as system, \
             patch.object(server, "capture", side_effect=fake_capture), \
             patch.object(server, "wait_ready", return_value="https://sample.trycloudflare.com",
                          side_effect=RuntimeError("startup failed") if fail_startup else None), \
             redirect_stdout(io.StringIO()):
            if fail_preflight or fail_startup:
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    server.deploy_locked(root, archive, digest, run)
            else:
                server.deploy_locked(root, archive, digest, run)
        self.assertEqual((root / "shop_key.pem").read_text(), "PRESERVE EXISTING KEY")
        return root, run, dropin, system

    def test_failed_preflight_does_not_stop_or_modify_the_existing_service(self):
        root, _, dropin, system = self.simulate_deployment(fail_preflight=True)
        self.assertNotIn(unittest.mock.call("stop", server.UNIT), system.call_args_list)
        self.assertEqual((root / "seller_r3.py").read_text(), "PREVIOUS SOURCE")
        self.assertEqual(dropin.read_text(), "PREVIOUS SERVICE CONFIG")

    def test_failed_startup_automatically_restores_old_service_and_source(self):
        root, run, dropin, system = self.simulate_deployment(fail_startup=True)
        self.assertEqual((root / "seller_r3.py").read_text(), "PREVIOUS SOURCE")
        self.assertEqual(dropin.read_text(), "PREVIOUS SERVICE CONFIG")
        self.assertFalse((root / "agent.json").exists())
        state = json.loads((root / ".market/deploy-backups" / run / "state.json").read_text())
        self.assertTrue(state["rolled_back"])
        self.assertEqual(system.call_args_list[-1], unittest.mock.call("start", server.UNIT))

    def test_confirmed_startup_keeps_release_and_records_success_evidence(self):
        root, run, dropin, _ = self.simulate_deployment()
        self.assertEqual((root / "seller_r3.py").read_text(), "# public source\n")
        self.assertIn("run_round3.py", dropin.read_text())
        result = json.loads((root / "logs" / ("deployment-" + run) / "result.json").read_text())
        self.assertEqual(result["kit"], "4")
        self.assertTrue(result["buyer_registered"] and result["shop_registered"] and result["acp_verified"])
