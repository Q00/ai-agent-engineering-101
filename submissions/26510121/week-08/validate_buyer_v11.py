"""Exercise proposed Buyer policy without changing the running market policy."""
import json
import pytest
from r3 import state

if __name__=='__main__':
    patch=json.loads((state.ROOT/'BUYER_V11_POLICY_PATCH.json').read_text(encoding='utf-8'))
    current=state.policy
    state.policy=lambda root=state.ROOT:dict(current(root),**patch)
    raise SystemExit(pytest.main(['tests','test_market_watch.py','-q']))
