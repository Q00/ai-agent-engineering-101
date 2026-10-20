"""Run the unmodified official market test on an identical, isolated source copy."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    repo = root.parents[2]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / "logs/shop-improvement/market-test" / run
    directory.mkdir(parents=True)
    sources = ("seller.py", "buyer.py", "market_buyer.py", "buyer_memory.py", "shop_catalog.py",
               "harness.py", "agent.json", "shop_policy.json", "review_event.json")
    hashes = {}
    for name in sources:
        shutil.copy2(root / name, directory / name)
        hashes[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    shutil.copytree(root / "prompts", directory / "prompts")
    for path in (directory / "prompts").glob("*.txt"):
        hashes[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    # The key stays inside this student's directory and is excluded by *.pem.
    shutil.copy2(root / "shop_key.pem", directory / "shop_key.pem")
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    print(f"Official market test on isolated source copy: {directory}", flush=True)
    try:
        result = subprocess.run([sys.executable, "-X", "utf8", "-u", str(repo / "scripts/market_test_week07.py"),
                                 str(directory)], cwd=repo, env=env)
    finally:
        (directory / "shop_key.pem").unlink(missing_ok=True)
    (directory / "source-manifest.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    if not (directory / "test-report.json").exists():
        return result.returncode or 1
    for name, digest in hashes.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Source changed during validation: {name}")
    report = json.loads((directory / "test-report.json").read_text(encoding="utf-8"))
    config = json.loads((root / "agent.json").read_text(encoding="utf-8"))
    assert report["seller_cmd"] == config["seller"]["cmd"]
    assert report["market_buyer_cmd"] == config["market_buyer"]["cmd"]
    archive = root / "logs/shop-improvement/pre-test" / run
    archive.mkdir(parents=True)
    if (root / "test-report.json").exists():
        shutil.copy2(root / "test-report.json", archive / "test-report.json")
    for path in (directory / "logs").glob("*.log"):
        target = root / "logs" / path.name
        if target.exists():
            shutil.copy2(target, archive / path.name)
        shutil.copy2(path, target)
    shutil.copy2(directory / "test-report.json", root / "test-report.json")
    print("Official report and raw market-test log copied to submission root; original evidence preserved.", flush=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
