import json
import os
import sys

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
try:
    posts = json.load(open(os.path.join(md, "forum.json"), encoding="utf-8"))
except OSError:
    posts = []
q = args.get("query") or ""
print(json.dumps([p for p in posts if q in str(p.get("text"))][:args.get("limit", 20)], ensure_ascii=False))
