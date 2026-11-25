import json
import os
import sys
from pathlib import Path
import strategy_screen

args = json.load(sys.stdin)
print(json.dumps(strategy_screen.build(Path(os.environ['MARKET_DIR']).resolve().parent,
                                       sku=args.get('sku')), ensure_ascii=False))
