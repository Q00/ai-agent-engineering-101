"""Kit hook for buyer orders (agent.json: market_buyer). The buyer business lives in depts/buyer_biz.py; this
file stays so the hook command and imports keep working."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import depts.buyer_biz as _m  # noqa: E402

if __name__ == "__main__":
    _m.main()
else:
    sys.modules[__name__] = _m
