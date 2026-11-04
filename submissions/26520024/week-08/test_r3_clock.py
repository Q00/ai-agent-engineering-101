"""Time skew tests never change the OS clock or use real HTTP."""
from email.utils import formatdate
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from r3_clock import clock, observe_date, offset_seconds, with_market_clock
from r3_common import atomic_json, load_inventory, read_json
from r3_market_kit import host_timestamped_writer
from r3_observe import refresh_public
from r3_restock import supply_window

BASE = 'https://offline.example'


class ClockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.md = Path(temporary.name)
        env = patch.dict(os.environ, {'MARKET_DIR': str(self.md)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('R3_CLOCK_OFFSET_SECONDS', None)
        clock._cache = None

    def response(self, stamp=1000, url=None, **headers):
        return httpx.Response(200, headers={'Date': formatdate(stamp, usegmt=True), **headers},
                              request=httpx.Request('GET', url or BASE+'/api/demand'))

    def test_correction_applies_to_expiry_windows_and_snapshot_age_only(self):
        original_host_time, original_monotonic = time.time, time.monotonic
        self.assertEqual(observe_date(self.response(), BASE, self.md, 1620), -620)
        with patch('r3_clock.host_time.time', return_value=1620):
            self.assertEqual(clock.time(), 1000)
            self.assertEqual(1100 - clock.time(), 100)
            inv = {'round': 3, 'balance': 100, 'units': [],
                   'restock': {'open': True, 'opens_at': 900, 'closes_at': 1100}}
            self.assertIsNotNone(supply_window(inv, clock.time()))
            host_timestamped_writer(atomic_json)(self.md/'inventory.json', inv)
            self.assertEqual(load_inventory(self.md), inv)
        self.assertIs(time.time, original_host_time)
        self.assertIs(clock.monotonic, original_monotonic)

    def test_untrusted_cached_slow_or_extreme_samples_are_rejected(self):
        for response, elapsed in ((self.response(url='https://other.example/api/demand'), 0),
                                  (self.response(Age='5'), 0), (self.response(Age='nan'), 0),
                                  (self.response(), 6), (self.response(), -1),
                                  (self.response(stamp=10000), 0)):
            with self.assertRaises(ValueError):
                observe_date(response, BASE, self.md, 1620, elapsed)
        self.assertFalse((self.md/'clock-offset.json').exists())

    def test_bootstrap_and_refresh_use_public_market_responses(self):
        calls = []
        def transport(request):
            calls.append((request.method, request.url.path))
            return httpx.Response(200, json=[], headers={'Date': formatdate(1000, usegmt=True)})
        class Base:
            def __init__(self, base, owner=None):
                self.base = base
                self.c = httpx.Client(transport=httpx.MockTransport(transport))
        with patch('r3_clock.host_time.time', return_value=1620), \
                patch('r3_clock.host_time.monotonic', return_value=100):
            market = with_market_clock(Base, self.md)(BASE)
        self.addCleanup(market.c.close)
        self.assertEqual(calls, [('GET', '/api/demand')])
        self.assertEqual(offset_seconds(self.md), -620)
        with patch('r3_clock.host_time.time', return_value=1621), \
                patch('r3_clock.host_time.monotonic', return_value=161):
            market.c.get(BASE+'/api/demand')
        self.assertEqual(read_json(self.md/'clock-offset.json')['observed_at_host'], 1621)

    def test_future_poll_marker_does_not_lose_ledger_position(self):
        atomic_json(self.md/'public-cursor.json', {'after': 42, 'polled_at': 1620})
        paths = []
        def get(path):
            paths.append(path)
            return []
        with patch.dict(os.environ, {'R3_ENABLE_LIVE': '1'}):
            result = refresh_public(self.md, get=get, now=1000)
        self.assertEqual(result['status'], 'updated')
        self.assertEqual(paths[0], '/api/ledger?after=42')
        self.assertEqual(read_json(self.md/'public-cursor.json'), {'after': 42, 'polled_at': 1000})

    def test_invalid_saved_offset_fails_instead_of_ignoring_expiry(self):
        atomic_json(self.md/'clock-offset.json', {'offset_seconds': 7200})
        with self.assertRaises(RuntimeError):
            offset_seconds(self.md)
