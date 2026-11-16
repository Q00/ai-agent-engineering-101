"""buyer.accept_grades: default keeps goods grade C refused; the key alone can widen it."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(SOURCE))
import buyer_policy as policy
import shop_strategy

goods = {"kind": "goods", "budget": 9000}
assert shop_strategy.DEFAULTS["buyer"]["accept_grades"] == ["A", "B"]
assert policy.accept_policy(goods, {"grade": "C"}, 10000)["grades"] == ["A", "B"]
assert not policy.hint_fits(goods, {"grade": "C"})
assert policy.hint_fits(goods, {"grade": "B"}) and policy.hint_fits(goods, {"grade": None})

real = policy.params
widened = lambda section: {**real(section), "accept_grades": ["A", "B", "C"]} if section == "buyer" else real(section)
with patch.object(policy, "params", side_effect=widened):
    accepted = policy.accept_policy(goods, {"grade": "C"}, 10000)
    assert accepted["grades"] == ["A", "B", "C"]
    assert accepted["max_price"] <= goods["budget"]
    assert policy.hint_fits(goods, {"grade": "C"})
print("PASS buyer.accept_grades defaults to A/B and only the key widens goods grades")
