"""Package explicitly selected source files; exclude keys, auth and live state.

Run with Python 3.10+ from any directory. The archive stays in ignored .market/;
the public manifest is a new file in logs/deployment-*/. Transfer shop/buyer keys
and memory separately over SSH, and never include private keys in a commit.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import uuid


def main():
    submission = Path(__file__).resolve().parents[1]
    repo = submission.parents[2]
    runtime = [
        "agent.json", "agent-round2.json", "shop-round2.json", ".gitignore",
        "seller_agent.py", "buyer_agent.py", "market_buyer.py", "harness.py",
        "receipt_signing.py", "public_market.py", "run_public_market.py",
        "check_public_shop.py", "verify_round2.py", "requirements.txt",
        "requirements-lock.txt", "test_harness.py", "test_seller_opening.py",
        "test_seller.py", "test_buyer.py", "test_market_buyer.py",
        "test_receipt_signing.py", "test_public_market.py", "test_shop_marketing.py",
    ]
    files = [submission / name for name in runtime]
    files += sorted((submission / "prompts").glob("*.txt"))
    files += sorted((submission / "deploy").glob("*.py"))
    files += [submission / "deploy/week07-market.service"]
    files += [repo / "scripts" / name for name in
              ["market_kit.py", "market_test_week07.py", "trade_test_week06.py", "check_week07.py"]]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = submission / ".market" / ("deployment-" + run)
    output.mkdir(parents=True)
    manifest = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
                "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    archive = output / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for source in files:
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(f"Expected a regular source file: {source}")
            name = str(source.relative_to(repo))
            manifest["files"][name] = hashlib.sha256(source.read_bytes()).hexdigest()
            tar.add(source, arcname=name, recursive=False)
    manifest["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    evidence = submission / "logs" / ("deployment-" + run)
    evidence.mkdir()
    with (evidence / "package-manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print(archive)
    print(evidence)
    print(f"Packaged {len(files)} source files, without keys, credentials, or live state.")


if __name__ == "__main__":
    main()
