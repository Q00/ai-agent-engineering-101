"""Reuse reviewed step2 isolation; explicitly include the untracked step3 tests."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = HERE.parent / '20261109-buyer-saved-strategy-step2/check.py'
code = source.read_text(encoding='utf-8').replace(
    "'test_buyer_saved_risk_offline.py'))",
    "'test_buyer_saved_risk_offline.py', 'test_buyer_food_precheck_offline.py'))")
exec(compile(code, str(HERE / 'check.py'), 'exec'),
     {'__name__': '__main__', '__file__': str(HERE / 'check.py')})
