import json
import os
import sys

import decision_memory

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
try:
    inv = json.load(open(os.path.join(md, "inventory.json"), encoding="utf-8"))
except (OSError, ValueError):
    inv = {}
print(json.dumps(decision_memory.outcomes(md, inv, last=args.get("last", 5), top=args.get("top", 10)), ensure_ascii=False))
