"""Verify/restore the committed log snapshot without overwriting existing logs."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "logs" / "push-ready-352360c"


def digest(stream):
    checksum = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        checksum.update(chunk)
        size += len(chunk)
    return checksum.hexdigest(), size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ARCHIVE / "manifest.json").read_text())
    planned = []
    for item in manifest["files"]:
        name = Path(item["original_path"]).name
        if name not in {"market-kit.log", "market-kit.log.1"}:
            raise ValueError("Unexpected log filename")
        archive = ARCHIVE / (name + ".gz")
        expected = (item["sha256_uncompressed"], item["original_bytes"])
        with gzip.open(archive, "rb") as stream:
            if digest(stream) != expected:
                raise ValueError(f"Archive integrity check failed: {archive.name}")
        target = ROOT / "logs" / name
        if not args.verify_only and target.exists():
            with target.open("rb") as stream:
                if digest(stream) != expected:
                    raise FileExistsError(f"Existing log differs; refusing to overwrite: {target}")
        planned.append((archive, target))
        print(f"Verified: {archive.name}")
    if args.verify_only:
        return
    for archive, target in planned:
        if target.exists():
            print(f"Already present: {target.name}")
            continue
        with target.open("xb") as output:
            try:
                with gzip.open(archive, "rb") as source:
                    shutil.copyfileobj(source, output)
            except BaseException:
                target.unlink()  # Only remove the incomplete file created above.
                raise
        print(f"Restored: {target.name}")


if __name__ == "__main__":
    main()
