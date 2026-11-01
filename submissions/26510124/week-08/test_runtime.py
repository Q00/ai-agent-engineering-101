from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from run_round3 import identity, verify_kit

ROOT = Path(__file__).resolve().parent


def write_key(path):
    path.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


class RuntimeTests(unittest.TestCase):
    def test_pinned_kit_exactly_matches_upstream_blob(self):
        self.assertTrue(verify_kit().is_file())

    def test_identity_copy_preserves_existing_keys_and_never_overwrites_mismatch(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            folder = Path(d)
            old, new = folder / "week07", folder / "week08"
            old.mkdir()
            new.mkdir()
            for name in ("shop_key.pem", "buyer_key.pem"):
                write_key(old / name)
            self.assertEqual(len(identity(old, new)), 2)
            self.assertFalse(list(new.iterdir()))
            identity(old, new, copy=True)
            for name in ("shop_key.pem", "buyer_key.pem"):
                self.assertEqual((old / name).read_bytes(), (new / name).read_bytes())
                self.assertEqual((new / name).stat().st_mode & 0o777, 0o600)
            (new / "shop_key.pem").unlink()
            write_key(new / "buyer_key.pem")
            with self.assertRaises(ValueError):
                identity(old, new, copy=True)
            self.assertFalse((new / "shop_key.pem").exists())

    def test_missing_source_key_never_generates_an_identity(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as d:
            folder = Path(d)
            old, new = folder / "old", folder / "new"
            old.mkdir()
            new.mkdir()
            write_key(old / "shop_key.pem")
            with self.assertRaises(FileNotFoundError):
                identity(old, new, copy=True)
            self.assertFalse(list(new.iterdir()))
