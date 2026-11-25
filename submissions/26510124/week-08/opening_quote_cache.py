"""Brief reuse of our own published opening price, never another buyer's bid."""
import hashlib
import json
import time


class OpeningQuoteCache:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.rows = {}

    @staticmethod
    def key(product, deal):
        # Any stock identity, quality, certificate or policy/bounds change misses.
        facts = {k: product.get(k) for k in ('unit', 'sku', 'kind', 'grade', 'cost',
                    'list_price', 'supplied_at', 'expires_at', 'cert')}
        payload = [deal['round'], facts, deal['terms'], deal['negotiation'], deal.get('advice'),
                   deal.get('ops_policy_version')]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()

    def get(self, key):
        now = self.clock()
        self.rows = {k: v for k, v in self.rows.items() if 0 <= now - v[0] < 60}
        row = self.rows.get(key)
        return {'action': 'counter', 'price': row[1]} if row else None

    def remember(self, key, price):
        if type(price) is not int or price <= 0:
            return
        self.get(key)  # Expire old entries; reads never extend their lifetime.
        self.rows.pop(key, None)
        while len(self.rows) >= 64:
            self.rows.pop(next(iter(self.rows)))
        self.rows[key] = (self.clock(), price)
