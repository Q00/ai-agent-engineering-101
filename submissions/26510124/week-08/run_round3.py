"""Check or explicitly join with the pinned kit. Never invent a new identity."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from receipt_signing import load_shop_key
from model_backend import CLAUDE_MODELS, CODEX_MODELS, advisor_enabled, load_profile, required_executables

ROOT = Path(__file__).resolve().parent


def verify_kit(root=ROOT):
    path = root / "vendor" / "market_kit_v4.py"
    raw = path.read_bytes()
    source = json.loads((path.parent / "SOURCE.json").read_text())
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != source["blob_sha"]:
        raise ValueError("Vendored kit differs from the recorded upstream blob")
    return path


def identity(source, target=ROOT, *, copy=False):
    """Validate BOTH keys before copying any; refuse to replace another identity."""
    planned = []
    for name in ("shop_key.pem", "buyer_key.pem"):
        old, new = source / name, target / name
        old_key = load_shop_key(old)
        if new.exists():
            if load_shop_key(new).public_key().public_bytes_raw() != old_key.public_key().public_bytes_raw():
                raise ValueError(f"{name} differs from week-07; refusing to overwrite")
        else:
            planned.append((old, new))
    if copy:
        for old, new in planned:
            fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(old.read_bytes())
    return [new.name for _, new in planned]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-from", type=Path, default=ROOT.parent / "week-07")
    parser.add_argument("--prepare-identity", action="store_true", help="Copy only existing matching keys, never generate keys")
    parser.add_argument("--join", action="store_true", help="Start seller, buyer and the configured restock hook")
    parser.add_argument("--orders", type=int, default=0)
    args = parser.parse_args()
    try:
        path = verify_kit()
        print("kit v4: pinned upstream blob verified", flush=True)
        missing = identity(args.identity_from.resolve(), copy=args.prepare_identity)
        if missing and not args.prepare_identity:
            print("Keys not prepared in week-08: " + ", ".join(missing), flush=True)
        else:
            print("shop/buyer identities match week-07", flush=True)
        # Match deployment's user CLI location without changing CLI credentials.
        os.environ["PATH"] = os.pathsep.join([str(Path(sys.executable).parent),
                                             str(Path.home() / ".local/bin"), os.environ.get("PATH", "")])
        profile = load_profile(ROOT)
        active_advisor = profile["advisor"] if advisor_enabled(ROOT) else "disabled"
        print(f"Model profile: shop/buyer={profile['trading']}; advisor={active_advisor}", flush=True)
        if profile["trading"] == "opus5.5":
            print(f"Pinned trading model: {CLAUDE_MODELS['opus5.5']}; effort=low; fallback disabled", flush=True)
        elif profile["trading"] in CODEX_MODELS:
            print(f"Pinned trading model: {CODEX_MODELS[profile['trading']]}; effort=low; fallback disabled", flush=True)
        unavailable = []
        for executable in required_executables(ROOT):
            available = shutil.which(executable) is not None
            print(f"{executable} CLI available: {available}", flush=True)
            if not available:
                unavailable.append(executable)
        print("Agent folder: " + str(ROOT), flush=True)
        if not args.join:
            print("Check only. No model calls, registrations or purchases.")
            return 0
        if missing and not args.prepare_identity:
            raise ValueError("Use --prepare-identity before joining")
        if not os.environ.get("MARKET_OWNER"):
            raise ValueError("MARKET_OWNER must be set in the existing private environment")
        if unavailable:
            raise ValueError("Required model CLI not found: " + ", ".join(unavailable)
                             + "; run model_backend.py --preflight before joining")
        if not shutil.which("cloudflared"):
            raise ValueError("cloudflared not found")
        if args.orders < 0:
            raise ValueError("orders must be nonnegative")
        (ROOT / ".market").mkdir(exist_ok=True)
        lock = (ROOT / ".market" / "runtime.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another week-08 launcher is already running")
        os.set_inheritable(lock.fileno(), True)
        # Every hook uses the same interpreter/venv as the launcher.
        os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        name = json.loads((ROOT / "shop-adaptive.json").read_text())["name"]
        try:
            os.execv(sys.executable, [sys.executable, str(ROOT / "market_runtime.py"), "join", "--dir", str(ROOT),
                                     "--market", "https://market.ourolabs.ai", "--name", name,
                                     "--tunnel", "--orders", str(args.orders)])
        finally:
            # A successful exec inherits the lock; a failed exec must close it.
            lock.close()
    except (OSError, ValueError) as exc:
        print(f"Not started: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
