"""Normal-round fixtures explicitly stay outside scheduled wind-down.

Real expiry/clearance behavior is exercised separately by test_shop_horizon.
Never modify the production policy or wall clock to make fixture quotes valid.
"""
from datetime import datetime, timezone
from unittest.mock import patch
from shop_horizon import clearance_bounds


def normal_market_horizon(case):
    def normal(config, rnd, product, terms, now):
        config = {**config, 'ends_at': datetime.fromtimestamp(now+30*86400, timezone.utc).isoformat()}
        return clearance_bounds(config, rnd, product, terms, now)
    active = patch('opening_strategy.clearance_bounds', side_effect=normal)
    active.start()
    case.addCleanup(active.stop)
