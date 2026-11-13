"""Official kit-managed free Quick Tunnel with health recovery and preserved shop identity."""
import os
import sys
from pathlib import Path

import market_kit as kit

ROOT = Path(__file__).resolve().parent
os.environ["PATH"] = str(ROOT / "runtime/bin") + ":/usr/local/bin:/usr/bin:/bin"
original_join = kit.Market.join


def tracked_join(self, role, name, key, url=None, tries=6):
    result = original_join(self, role, name, key, url, tries)
    if role == "shop" and url:
        path = ROOT / ".market/public-url"
        temp = path.with_suffix(".next")
        temp.write_text(url + "\n")
        temp.replace(path)
    return result


kit.Market.join = tracked_join
sys.argv = ["market_kit.py", "join", "--dir", str(ROOT), "--market", "https://market.ourolabs.ai",
            "--name", "cheapest-in-town", "--shop-only", "--tunnel", "--port", "9308"]
kit.main()
