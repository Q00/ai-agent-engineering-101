"""Combined forum cadence: forum_r3 (legacy) must count terra-forum-v1 intents too.

review_r3 runs forum_r3.maybe_report whenever forum_terra.active() is False (terra config off, or the source-bound
restock_r3 gate closed during a deploy). forum_r3 used to count only its own intents against its own cap (8), so a
terra post 10 minutes earlier, or 6 combined posts in 24 h, did not stop it. The combined rule is: at least 60 minutes
after the last post of either writer and at most 6 posts of both writers together per rolling 24 h.
The authentic case replays the actual 2026-11-12 records (6 legacy intents + 1 terra intent within 24 h).
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_forum_policy_v1 import T0, make_state, no_network, outbox, settle
from test_forum_policy_v2 import V2, set_policy

HERE = Path(__file__).resolve().parent
SNAP = HERE/'.market'
NOW_LIVE = 1794481608  # 2026-11-12 20:06:48 KST, when the snapshot records were read


def terra_record(*ats):
    return {'version':1, 'considered':{}, 'calls':list(ats),
            'intents':{str(900+i):{'at':at, 'status':'verified'} for i, at in enumerate(ats)}}


class ForumCombinedCap(unittest.TestCase):
    def state(self, root, terra=None):
        state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
        set_policy(state, V2)
        if terra is not None:
            (state/'forum-terra-report.json').write_text(json.dumps(terra), encoding='utf-8')
        return state

    def test_recent_terra_post_holds_legacy_rate_window(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000)]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = self.state(root, terra_record(T0-600))   # terra posted 10 minutes ago
            got = forum_r3.maybe_report(state, now=T0, rows=rows)
            self.assertFalse(got.get('queued'), got)
            self.assertEqual(outbox(state), [])
        with tempfile.TemporaryDirectory() as root, no_network():
            state = self.state(root, terra_record(T0-3601))  # 60 min later: allowed again
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)

    def test_combined_rolling_24h_cap_is_six(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000)]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = self.state(root, terra_record(*(T0-2*3600*k for k in range(1, 7))))  # 6 terra posts, oldest 12 h
            got = forum_r3.maybe_report(state, now=T0, rows=rows)
            self.assertFalse(got.get('queued'), got)
            self.assertEqual(outbox(state), [])
        with tempfile.TemporaryDirectory() as root, no_network():
            state = self.state(root, terra_record(*(T0-2*3600*k for k in range(1, 6))))  # 5: one slot left
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)

    def test_unreadable_terra_record_fails_closed(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000)]
        for bad in ('{not json', json.dumps({'version':1, 'intents':{'1':{'at':'yesterday'}}}),
                    json.dumps({'version':1, 'intents':[]})):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as root, no_network():
                state = self.state(root)
                (state/'forum-terra-report.json').write_text(bad, encoding='utf-8')
                self.assertFalse(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'))
                self.assertEqual(outbox(state), [])

    @unittest.skipUnless((SNAP/'forum-report.json').is_file() and (SNAP/'forum-terra-report.json').is_file(),
                         'staged .market snapshot records missing')
    def test_actual_20261112_records_already_at_combined_cap(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state = Path(root)/'.market'
            state.mkdir()
            for name in ('forum-report.json', 'forum-terra-report.json', 'forum-policy.json'):
                shutil.copyfile(SNAP/name, state/name)
            legacy = json.loads((state/'forum-report.json').read_text(encoding='utf-8'))
            terra = json.loads((state/'forum-terra-report.json').read_text(encoding='utf-8'))
            day = lambda rec, t: sum(t-i['at'] < 24*3600 for i in rec['intents'].values())
            legacy_max = json.loads((state/'forum-policy.json').read_text())['max_intents_per_24h']
            last = max(i['at'] for rec in (legacy, terra) for i in rec['intents'].values())
            # At NOW_LIVE legacy alone is at its own cap (8). Replay the first real instant after a legacy intent
            # leaves the 24 h window: legacy alone then allows a post, combined is still >= 6.
            exits = sorted(i['at']+24*3600+1 for i in legacy['intents'].values() if i['at']+24*3600+1 > NOW_LIVE)
            when = next((t for t in exits if t-last >= 3600 and day(legacy, t) < legacy_max
                         and day(legacy, t)+day(terra, t) >= 6), None)
            self.assertIsNotNone(when, 'actual records never reach the legacy-allows/combined-full state')
            ledger = 99_000_001                                          # a fresh own paid ledger, novel SKU
            (state/'payments.jsonl').write_text(json.dumps(dict(order='o-new', shop=settle(0, '', '', 0)['data']['shop'],
                                                                item='SKUNEW', price=70000, paid=True, ledger=ledger))
                                                + '\n', encoding='utf-8')
            (state/'catalog.json').write_text(json.dumps({'SKUNEW':{'name':'New Item'}}), encoding='utf-8')
            row = dict(settle(ledger, 'o-new', 'SKUNEW', 70000), ts=when-60)
            got = forum_r3.maybe_report(state, now=when, rows=[row])
            self.assertFalse(got.get('queued'), got)
            self.assertEqual(outbox(state), [])


if __name__ == '__main__':
    unittest.main()
