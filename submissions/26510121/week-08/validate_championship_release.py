"""Run the whole suite with proposed policy in THIS process, not live policy."""
import json
import pytest
from r3 import state


if __name__=='__main__':
    patch=json.loads((state.ROOT/'CHAMPIONSHIP_POLICY_PATCH.json').read_text(encoding='utf-8'))
    current_policy=state.policy
    state.policy=lambda root=state.ROOT:dict(current_policy(root),**patch)
    raise SystemExit(pytest.main(['tests','test_market_watch.py','-q']))
