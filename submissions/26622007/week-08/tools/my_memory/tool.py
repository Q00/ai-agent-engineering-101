import json
import sys

import agent_memory

args = json.load(sys.stdin)
print(json.dumps({"hits": agent_memory.search(str(args.get("query") or ""), int(args.get("limit") or 20))}, ensure_ascii=False))
