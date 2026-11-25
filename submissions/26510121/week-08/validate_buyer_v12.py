"""Validate the next Buyer policy without mutating live policy."""
import pytest
from r3 import state

if __name__=='__main__':
    patch=state.read_json(state.ROOT/'BUYER_V12_POLICY_PATCH.json',{})
    current=state.policy
    state.policy=lambda root=state.ROOT:dict(current(root),**patch)
    raise SystemExit(pytest.main(['tests','test_market_watch.py','-q']))
