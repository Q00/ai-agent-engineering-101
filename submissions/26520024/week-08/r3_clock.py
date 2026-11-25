"""Market-domain wall clock correction; OS time and monotonic deadlines are unchanged."""
from email.utils import parsedate_to_datetime
import math
import os
import time as host_time
from urllib.parse import urlsplit


def offset_seconds(md=None):
    from r3_common import read_json, state_dir
    override = os.environ.get('R3_CLOCK_OFFSET_SECONDS')
    if override is not None:
        value = float(override)
    else:
        data = read_json(state_dir(md) / 'clock-offset.json', {})
        value = data.get('offset_seconds', 0)
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 3600:
        raise RuntimeError('Invalid market clock offset; synchronize the host clock before starting')
    return value


class MarketClock:
    def __init__(self):
        self._cache = None

    def time(self):
        from r3_common import state_dir
        key = (state_dir(), os.environ.get('R3_CLOCK_OFFSET_SECONDS'))
        tick = host_time.monotonic()
        cache = self._cache
        if cache is None or cache[0] != key or tick >= cache[1]:
            cache = (key, tick + 1, offset_seconds())
            self._cache = cache
        return host_time.time() + cache[2]

    def __getattr__(self, name):
        return getattr(host_time, name)


clock = MarketClock()


def observe_date(response, base, md, received_at, elapsed=0):
    from r3_common import atomic_json, state_dir
    target, source = urlsplit(base), urlsplit(str(response.request.url))
    if target.scheme != 'https' or (source.scheme, source.netloc) != (target.scheme, target.netloc):
        raise ValueError('Only the configured HTTPS market can supply the clock')
    age = float(response.headers.get('age', '0'))
    if (response.status_code >= 400 or not math.isfinite(elapsed) or not 0 <= elapsed <= 5 or
            not math.isfinite(age) or age != 0 or not math.isfinite(received_at)):
        raise ValueError('Unreliable or cached market clock response')
    parsed = parsedate_to_datetime(response.headers.get('date', ''))
    if parsed.tzinfo is None:
        raise ValueError('Market Date must contain a timezone')
    offset = parsed.timestamp() - (received_at - elapsed / 2)
    if not math.isfinite(offset) or abs(offset) > 3600:
        raise ValueError('Market clock differs by more than one hour; operator action required')
    atomic_json(state_dir(md) / 'clock-offset.json',
                {'offset_seconds': offset, 'observed_at_host': received_at,
                 'date': response.headers['date'], 'market': base,
                 'uncertainty_seconds': 1 + elapsed / 2})
    clock._cache = None
    return offset


def with_market_clock(base_class, md, log=lambda text: None):
    class ClockMarket(base_class):
        def __init__(self, base, owner=None):
            super().__init__(base, owner)
            if urlsplit(base).scheme != 'https':
                raise ValueError('Market clock calibration requires HTTPS')
            before = host_time.monotonic()
            response = self.c.get(self.base + '/api/demand', headers={'Cache-Control': 'no-cache'}, timeout=5)
            value = observe_date(response, self.base, md, host_time.time(), host_time.monotonic() - before)
            log(f'[clock] market minus host {value:+.1f}s; OS clock unchanged')
            self._clock_checked = host_time.monotonic()
            self.c.event_hooks['response'].append(self._observe_clock)

        def _observe_clock(self, response):
            tick = host_time.monotonic()
            if tick - self._clock_checked < 60:
                return
            try:
                observe_date(response, self.base, md, host_time.time())
            except (ValueError, TypeError, OverflowError, OSError):
                return
            self._clock_checked = tick
    return ClockMarket
