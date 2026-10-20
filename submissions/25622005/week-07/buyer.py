"""Week-06 CLI buyer; shared negotiation also serves the week-07 market buyer."""
import argparse
import json

from negotiation import negotiate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    args = parser.parse_args()
    result = negotiate(args.url, args.item, args.budget)
    print(json.dumps({"state": result["state"], "artifacts": result["artifacts"]}), flush=True)


if __name__ == "__main__":
    main()
