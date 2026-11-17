"""Single local kit launcher; preserves keys and leaves the configured hold authoritative.

python run_market.py --check
python run_market.py --market-run round3 -- join --dir . --market https://market.ourolabs.ai --name NAME --tunnel
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT / "restock"))
from run_market_restock import install_runtime, REVIEWED_KIT_SHA256
from restock_policy import Config


def load_forum_sender():
    package = types.ModuleType("week08_forum")
    package.__path__ = [str(ROOT / "buyer-forum")]
    sys.modules[package.__name__] = package
    from week08_forum.kit_runner import attach_approved_sender
    return attach_approved_sender


def load_kit(path):
    if hashlib.sha256(path.read_bytes()).hexdigest() != REVIEWED_KIT_SHA256:
        raise ValueError("kit hash changed; review its API before launching")
    spec = importlib.util.spec_from_file_location("week08_official_kit", path)
    kit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = kit
    spec.loader.exec_module(kit)
    return kit


def attach(kit, state, market_run, cfg):
    install_runtime(kit, state, market_run, cfg)
    load_forum_sender()(kit)


def check(root, kit_path):
    issues = []
    if not kit_path.is_file() or hashlib.sha256(kit_path.read_bytes()).hexdigest() != REVIEWED_KIT_SHA256:
        issues.append("reviewed official kit missing or changed")
    for name in ("shop_key.pem", "buyer_key.pem"):
        if not (root / name).is_file():
            issues.append(f"existing {name} missing; no replacement identity will be generated")
    if not os.getenv("SHOP_PRIVATE_KEY_PEM") and not (root / "seller/shop_key.pem").is_file():
        issues.append("seller signing key unavailable at its unchanged location; configure SHOP_PRIVATE_KEY_PEM for the existing shop identity")
    if (root / "shop_key.pem").is_file():
        try:
            from cryptography.hazmat.primitives import serialization
            actual = serialization.load_pem_private_key((root / "shop_key.pem").read_bytes(), password=None)
            supplied = os.getenv("SHOP_PRIVATE_KEY_PEM")
            signing_bytes = supplied.replace("\\n", "\n").encode() if supplied else (root / "seller/shop_key.pem").read_bytes()
            signing = serialization.load_pem_private_key(signing_bytes, password=None)
            raw = lambda key: key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            if raw(actual) != raw(signing):
                issues.append("seller signing identity differs from the kit shop identity; keys were not changed")
        except (OSError, ValueError, TypeError):
            issues.append("could not verify the existing signing identity")
    for name in ("node_modules/.bin/tsx", "harness/decide.py", "seller/seller_r3.py"):
        if not (root / name).exists():
            issues.append(f"missing {name}")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=ROOT.parents[2] / "scripts/market_kit.py")
    parser.add_argument("--market-run", default="round3")
    parser.add_argument("--config", type=Path, default=ROOT / "restock/small_batch.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("kit_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    forwarded = args.kit_args[1:] if args.kit_args[:1] == ["--"] else args.kit_args
    if args.check:
        issues = check(ROOT, args.kit)
        print(json.dumps({"ready": not issues, "issues": issues, "restock": json.loads((ROOT / "agent.json").read_text())["restock"]}, indent=2))
        return 1 if issues else 0
    if not forwarded or forwarded[0] != "join":
        parser.error("pass join arguments after --")
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("--dir", default=str(ROOT))
    probe.add_argument("--market", default="https://market.ourolabs.ai")
    probe.add_argument("--deployed")
    known, _ = probe.parse_known_args(forwarded[1:])
    if Path(known.dir).resolve() != ROOT or known.deployed:
        parser.error("use this submission directory and the local kit proxy")
    issues = check(ROOT, args.kit)
    if issues:
        parser.error("; ".join(issues))
    # Keep all kit-created files inside this submission even when invoked elsewhere.
    if not any(a == "--dir" or a.startswith("--dir=") for a in forwarded):
        forwarded += ["--dir", str(ROOT)]
    os.environ["MARKET_URL"] = known.market.rstrip("/")
    state = ROOT / ".market"
    state.mkdir(exist_ok=True)
    lock = state / "restock_runtime.lock"
    lock.mkdir()  # fail closed on another launcher or a stale lock
    child = None
    try:
        kit = load_kit(args.kit.resolve())
        cfg = Config(**json.loads(args.config.read_text())) if args.config else Config()
        attach(kit, state, args.market_run, cfg)
        (ROOT / "logs").mkdir(exist_ok=True)
        with (ROOT / "logs/forum-analysis.log").open("a") as output:
            # Independent process: public-ledger latency never blocks checkout/restock.
            child = subprocess.Popen([sys.executable, str(ROOT / "buyer-forum/analyst.py"),
                                      "--state", str(state), "--market", known.market],
                                     cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            sys.argv = [str(args.kit), *forwarded]
            kit.main()
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        lock.rmdir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
