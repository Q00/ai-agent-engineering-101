"""Extract only *observed* seller authority-spoofing messages from market-kit.log.

Read-only original log. Writes an audit JSON with the original line number,
exact text and SHA-256 digest; does not invent any market transactions.
Run from week-07 folder. The output is evidence, not a simulated trade.
"""
import hashlib
import json
from pathlib import Path

from market_buyer import suspicious_authority_message

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "logs" / "market-kit.log"
DEST = ROOT / "logs" / "challenge-2" / "observed-spoofing.json"


def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Missing original market log: {SOURCE}")
    observations = []
    with SOURCE.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if "[a2a recv]" not in line or not suspicious_authority_message(line):
                continue
            # A response line does not always identify the remote shop;
            # rely only on a directly preceding logged selected shop.
            observations.append({
                "line": line_number,
                "raw": line.rstrip("\n"),
                "evidence_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                "shop": None,
            })
    # Correlate with the last selected shop in the same market buyer run.
    if observations:
        last = None
        import re
        with SOURCE.open(encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                m = re.search(r"\[buyer\] \[shop selected\] name='([^']+)'", line)
                if m:
                    last = m.group(1)
                if any(x["line"] == line_number for x in observations):
                    shop = last
                    target = next(x for x in observations if x["line"] == line_number)
                    target["shop"] = shop
    observations = [o for o in observations if o.get("shop")]
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps({
        "source": "unaltered market-kit.log",
        "source_path": "logs/market-kit.log",
        "observations": observations,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(observations)} actual authority-spoof messages at {DEST}")
    for event in observations[:10]:
        print("line", event["line"], "shop", event["shop"])


if __name__ == "__main__":
    main()
