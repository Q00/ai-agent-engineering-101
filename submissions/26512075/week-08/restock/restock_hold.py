"""
Temporary Market Kit v4 restock hook: choose not to buy any stock.
"""

from __future__ import annotations

import argparse
import json

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, help="market kit market path")
    parser.parse_args()

    print(json.dumps({"buy": []}))


if __name__ == "__main__":
    main()




