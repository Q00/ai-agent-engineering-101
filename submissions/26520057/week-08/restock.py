"""Demand-based course-credit restock; bounded capital, no bankruptcy or paid models."""
import argparse
import json
from pathlib import Path
from shop_strategy import restock_plan as plan

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(plan(args.state)))
    except Exception:
        print('{"buy": []}')
