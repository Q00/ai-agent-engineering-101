"""Prepare a reviewable seller-only agent.json candidate; never activate it."""
import argparse
import difflib
import json
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week07", type=Path, default=ROOT.parent / "week-07")
    args = parser.parse_args()
    old = args.week07.resolve()
    source = old / "agent.json"
    original = source.read_text()
    agent = json.loads(original)
    for key in ("seller", "buyer", "market_buyer"):
        if not isinstance(agent.get(key, {}).get("cmd"), str):
            parser.error(f"Missing {key} command in {source}")
    command = [str(old / ".venv/bin/python"), str(ROOT / "seller_agent.py"),
               "--config", str(ROOT / "shop-adaptive.json"), "--port", "{port}",
               "--sign-receipts", "--shop-key", str(old / "shop_key.pem")]
    # The kit substitutes {port} as a shell-quoted value. Keep this token unquoted.
    agent["seller"]["cmd"] = " ".join(x if x == "{port}" else shlex.quote(x) for x in command)
    candidate = json.dumps(agent, ensure_ascii=False, indent=2) + "\n"
    output = ROOT / ".market" / "agent-with-adaptive-seller.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(candidate)
    print("".join(difflib.unified_diff(original.splitlines(True), candidate.splitlines(True),
                                       fromfile=str(source), tofile=str(output))))
    print("Candidate only; no service or existing agent.json was changed.")


if __name__ == "__main__":
    main()
